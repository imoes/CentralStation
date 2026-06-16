"""Bridge — single glanceable status endpoint for the starship-cockpit view.

GET /api/bridge/status returns everything the bridge needs in ONE call:
  - overall alert state (red / yellow / green)
  - severity counts (critical / high / medium / total)
  - per-source system status (checkmk / graylog / wazuh)
  - per-location sector status
  - the single most important active incident (+ AI insight)
  - a short live sensor log (latest alerts)

Designed to be polled every ~10s by the bridge frontend.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db

router = APIRouter(prefix="/bridge", tags=["bridge"])
log = logging.getLogger(__name__)

_SOURCES = ["checkmk", "graylog", "wazuh"]
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _state_from_counts(critical: int, high: int) -> str:
    if critical > 0:
        return "red"
    if high > 0:
        return "yellow"
    return "green"


# Percentage metrics we forecast toward a breach threshold
_FORECAST_METRICS = {
    "fs_used_percent":  {"label": "Disk", "threshold": 100.0, "warn_at": 80.0},
    "mem_used_percent": {"label": "RAM",  "threshold": 95.0,  "warn_at": 85.0},
}
# Metrics shown as current fleet vitals (highest pressure first)
_VITAL_METRICS = {
    "fs_used_percent":  {"label": "Disk", "unit": "%"},
    "mem_used_percent": {"label": "RAM",  "unit": "%"},
    "load1":            {"label": "CPU",  "unit": ""},
}


def _linreg_eta(points: list[tuple[float, float]], threshold: float) -> float | None:
    """Given (timestamp_sec, value) points, project linearly to `threshold`.
    Returns hours-until-threshold (>0) or None if not trending toward it."""
    n = len(points)
    if n < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    ss_xx = sum((x - mx) ** 2 for x in xs)
    if ss_xx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / ss_xx  # value per second
    if slope <= 0:
        return None  # not growing
    current = ys[-1]
    if current >= threshold:
        return 0.0
    seconds_to = (threshold - current) / slope
    hours = seconds_to / 3600
    return hours if hours >= 0 else None


async def _compute_metrics(os_client, host_scope: list[str] | None = None) -> tuple[list, list]:
    """Compute fleet vitals (current pressure) + forecast warnings (projected breaches)
    from the cs-metrics-checkmk index. Pure CPU math on stored time series."""
    vitals: list[dict] = []
    forecasts: list[dict] = []
    try:
        from dateutil.parser import parse as _parse
        # Pull recent metric points; group by host+metric, keep the series
        query: dict = {"range": {"timestamp": {"gte": "now-12h"}}}
        if host_scope:
            hosts = [h for h in host_scope if h]
            if hosts:
                query = {
                    "bool": {
                        "must": [{"range": {"timestamp": {"gte": "now-12h"}}}],
                        "filter": [{"terms": {"host": hosts}}],
                    }
                }

        resp = await os_client.search(
            index="cs-metrics-checkmk",
            body={
                "query": query,
                "size": 0,
                "aggs": {
                    "by_metric": {
                        "terms": {"field": "metric", "size": 10},
                        "aggs": {
                            "by_host": {
                                "terms": {"field": "host", "size": 200},
                                "aggs": {
                                    "series": {
                                        "top_hits": {
                                            "size": 30,
                                            "sort": [{"timestamp": {"order": "asc"}}],
                                            "_source": ["value", "timestamp", "unit"],
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
            ignore_unavailable=True,
        )
        metric_buckets = resp.get("aggregations", {}).get("by_metric", {}).get("buckets", [])

        for mb in metric_buckets:
            metric = mb["key"]
            for hb in mb.get("by_host", {}).get("buckets", []):
                host = hb["key"]
                hits = hb.get("series", {}).get("hits", {}).get("hits", [])
                series = []
                for h in hits:
                    src = h["_source"]
                    try:
                        ts = _parse(src["timestamp"]).timestamp()
                        series.append((ts, float(src["value"])))
                    except Exception:
                        pass
                if not series:
                    continue
                current = series[-1][1]
                unit = (hits[-1]["_source"].get("unit") or "")

                # Vitals (current pressure)
                if metric in _VITAL_METRICS:
                    vitals.append({
                        "host": host, "metric": metric,
                        "label": _VITAL_METRICS[metric]["label"],
                        "value": round(current, 1),
                        "unit": _VITAL_METRICS[metric]["unit"] or unit,
                    })

                # Forecast (projected breach)
                if metric in _FORECAST_METRICS:
                    cfg = _FORECAST_METRICS[metric]
                    eta = _linreg_eta(series, cfg["threshold"])
                    # only warn if currently elevated AND trending to breach within 48h
                    if eta is not None and current >= cfg["warn_at"] and eta <= 48:
                        forecasts.append({
                            "host": host, "metric": metric,
                            "label": cfg["label"],
                            "current": round(current, 1),
                            "threshold": cfg["threshold"],
                            "eta_hours": round(eta, 1),
                        })
    except Exception as e:
        log.warning("bridge: metrics computation failed: %s", e)

    # Top vitals per metric (highest first), max 4 per metric
    top_vitals: list[dict] = []
    for metric in _VITAL_METRICS:
        rows = sorted([v for v in vitals if v["metric"] == metric], key=lambda x: -x["value"])[:4]
        top_vitals.extend(rows)

    # Forecasts: soonest breach first
    forecasts.sort(key=lambda f: f["eta_hours"])
    return top_vitals, forecasts[:6]


@router.get("/status")
async def bridge_status(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: int = Query(6, ge=1, le=72),
):
    """Aggregate the whole situational picture for the cockpit in one response."""
    from app.core.opensearch import get_opensearch

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    os_client = get_opensearch()

    # ── 0. User CheckMK scope (OS/location/VE/criticality filter) ─────────────
    # Computed FIRST so every element below (counts, incidents, vitals, logs)
    # respects it. Empty list = no filter active → show everything.
    from app.services.feed_index import get_user_checkmk_host_scope, get_exclusion_must_not_clauses
    host_scope = await get_user_checkmk_host_scope(db, str(user.id))

    # Exclusion FeedSearches (system + this user's personal ignores) — applied to
    # every query below so the Bridge respects the same hide-filters as the Feed.
    try:
        exclusion_must_not = await get_exclusion_must_not_clauses(db, str(user.id))
    except Exception as e:
        log.warning("bridge_status: exclusion clauses failed: %s", e)
        exclusion_must_not = []

    def _must_not() -> list[dict]:
        """Standard must_not: hide resolved items + all active exclusion filters."""
        return [{"term": {"status": "resolved"}}, *exclusion_must_not]

    def _scope_filter() -> list[dict]:
        """OpenSearch filter clause restricting to the user's CheckMK host scope.

        Matches the feed's pattern (host / agent / host_candidates) so the Bridge
        and the News Feed apply the SAME single-source-of-truth scoping.
        """
        hosts = [h for h in (host_scope or []) if h]
        if not hosts:
            return []
        return [{
            "bool": {
                "should": [
                    {"terms": {"metadata.host.keyword": hosts}},
                    {"terms": {"metadata.agent.keyword": hosts}},
                    {"terms": {"metadata.host_candidates.keyword": hosts}},
                ],
                "minimum_should_match": 1,
            }
        }]

    # ── 1. Aggregations: severity, per-source, per-location ──────────────────
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    sources: dict[str, dict] = {s: {"critical": 0, "high": 0, "total": 0} for s in _SOURCES}
    sectors: dict[str, dict] = {}

    try:
        resp = await os_client.search(
            index="cs-feed-*",
            body={
                "query": {
                    "bool": {
                        "must": [{"range": {"created_at": {"gte": since}}}],
                        "filter": _scope_filter(),
                        "must_not": _must_not(),
                    }
                },
                "size": 0,
                "aggs": {
                    "by_sev": {"terms": {"field": "severity", "size": 10}},
                    "by_source": {
                        "terms": {"field": "source", "size": 10},
                        "aggs": {"sev": {"terms": {"field": "severity", "size": 10}}},
                    },
                    # location_name is empty for CheckMK (ID-Generator enrichment only
                    # runs for Graylog/Wazuh). Use the CheckMK metadata.location folder
                    # field instead — it contains the site path (e.g. "muenchen", "kassel").
                    "by_location": {
                        "terms": {"field": "location_city", "size": 30},
                        "aggs": {"sev": {"terms": {"field": "severity", "size": 10}}},
                    },
                    "by_checkmk_location": {
                        "filter": {"term": {"source": "checkmk"}},
                        "aggs": {
                            "locs": {
                                "terms": {"field": "metadata.location.keyword", "size": 30},
                                "aggs": {"sev": {"terms": {"field": "severity", "size": 10}}},
                            }
                        },
                    },
                },
            },
            ignore_unavailable=True,
        )
        aggs = resp.get("aggregations", {})

        for b in aggs.get("by_sev", {}).get("buckets", []):
            if b["key"] in counts:
                counts[b["key"]] = b["doc_count"]

        for b in aggs.get("by_source", {}).get("buckets", []):
            src = b["key"]
            if src not in sources:
                sources[src] = {"critical": 0, "high": 0, "total": 0}
            sources[src]["total"] = b["doc_count"]
            for sev in b.get("sev", {}).get("buckets", []):
                if sev["key"] == "critical":
                    sources[src]["critical"] = sev["doc_count"]
                elif sev["key"] == "high":
                    sources[src]["high"] = sev["doc_count"]

        # location_city (Graylog/Wazuh) → sectors
        for b in aggs.get("by_location", {}).get("buckets", []):
            loc = b["key"]
            if not loc:
                continue
            crit = high = 0
            for sev in b.get("sev", {}).get("buckets", []):
                if sev["key"] == "critical": crit = sev["doc_count"]
                elif sev["key"] == "high": high = sev["doc_count"]
            sectors[loc] = {"critical": crit, "high": high, "total": b["doc_count"]}

        # metadata.location.keyword (CheckMK folder paths) → sectors
        for b in aggs.get("by_checkmk_location", {}).get("locs", {}).get("buckets", []):
            loc = (b["key"] or "").strip().strip("/").replace("/", " › ")
            if not loc or len(loc) < 2:
                continue
            crit = high = 0
            for sev in b.get("sev", {}).get("buckets", []):
                if sev["key"] == "critical": crit = sev["doc_count"]
                elif sev["key"] == "high": high = sev["doc_count"]
            if loc in sectors:
                sectors[loc]["critical"] += crit
                sectors[loc]["high"] += high
                sectors[loc]["total"] += b["doc_count"]
            else:
                sectors[loc] = {"critical": crit, "high": high, "total": b["doc_count"]}
    except Exception as e:
        log.warning("bridge_status: aggregation failed: %s", e)

    total = sum(counts.values())
    alert_state = _state_from_counts(counts["critical"], counts["high"])

    # ── 2. Sensor log: latest 8 regardless of severity ───────────────────────
    sensor_log: list[dict] = []
    try:
        log_resp = await os_client.search(
            index="cs-feed-*",
            body={
                "query": {
                    "bool": {
                        "must": [{"range": {"created_at": {"gte": since}}}],
                        "filter": _scope_filter(),
                        "must_not": _must_not(),
                    }
                },
                "size": 8,
                "sort": [{"created_at": {"order": "desc"}}],
            },
            ignore_unavailable=True,
        )
        for h in log_resp.get("hits", {}).get("hits", []):
            src = h["_source"]
            meta = src.get("metadata") or {}
            sensor_log.append({
                "severity": src.get("severity", "info"),
                "source": src.get("source", ""),
                "title": (src.get("title") or "")[:90],
                "host": meta.get("host") or meta.get("agent") or meta.get("container_name") or "",
                "created_at": src.get("created_at"),
            })
    except Exception as e:
        log.warning("bridge_status: sensor log query failed: %s", e)

    # ── 3. Primary incident: newest alert of the highest PRESENT severity ─────
    primary_incident = None
    target_sev = None
    for sev in ("critical", "high", "medium"):
        if counts.get(sev, 0) > 0:
            target_sev = sev
            break
    if target_sev:
        try:
            inc_resp = await os_client.search(
                index="cs-feed-*",
                body={
                    "query": {
                        "bool": {
                            "must": [
                                {"range": {"created_at": {"gte": since}}},
                                {"term": {"severity": target_sev}},
                            ],
                            "filter": _scope_filter(),
                            "must_not": _must_not(),
                        }
                    },
                    "size": 1,
                    "sort": [{"created_at": {"order": "desc"}}],
                },
                ignore_unavailable=True,
            )
            inc_hits = inc_resp.get("hits", {}).get("hits", [])
            if inc_hits:
                top = inc_hits[0]["_source"]
                meta = top.get("metadata") or {}
                primary_incident = {
                    "severity": top.get("severity", "info"),
                    "source": top.get("source", ""),
                    "title": top.get("title", ""),
                    "host": meta.get("host") or meta.get("agent") or meta.get("container_name") or "",
                    "location": top.get("location_name") or "",
                    "ai_insight": top.get("ai_insight") or "",
                    "created_at": top.get("created_at"),
                }
        except Exception as e:
            log.warning("bridge_status: primary incident query failed: %s", e)

    # ── 3. Build source + sector lists with state ────────────────────────────
    source_list = [
        {
            "name": s,
            "state": _state_from_counts(d["critical"], d["high"]),
            "critical": d["critical"],
            "high": d["high"],
            "total": d["total"],
        }
        for s, d in sources.items()
    ]
    sector_list = sorted(
        [
            {
                "name": loc,
                "state": _state_from_counts(d["critical"], d["high"]),
                "critical": d["critical"],
                "high": d["high"],
                "total": d["total"],
            }
            for loc, d in sectors.items()
        ],
        key=lambda x: (-_SEV_RANK.get("critical" if x["critical"] else ("high" if x["high"] else "info"), 0), x["name"]),
    )[:8]

    # ── 4. Fleet vitals + forecast warnings (from cs-metrics-checkmk) ────────
    # host_scope already computed in section 0 (applied to all elements).
    vitals, forecasts = await _compute_metrics(os_client, host_scope)

    # ── 5. AI-prioritised worklist — build fresh for this user's scope ─────────
    # We rebuild on each status call only if no recent snapshot exists (< 16 min).
    # This ensures the worklist always respects the requesting user's CheckMK filters.
    from app.services.worklist_builder import get_latest_worklist, build_worklist
    from app.services.settings import get_agent_config as _get_ac
    worklist = await get_latest_worklist(db)
    rebuild_needed = (
        worklist is None
        or not worklist.get("created_at")
        or (datetime.now(timezone.utc) - datetime.fromisoformat(worklist["created_at"].replace("Z", "+00:00"))) > timedelta(minutes=16)
    )
    if rebuild_needed:
        try:
            _cfg = await _get_ac(db)
            worklist = await build_worklist(db, hours=24, size=_cfg.worklist_size, user_id=str(user.id))
        except Exception as _e:
            log.debug("bridge: worklist rebuild failed: %s", _e)

    # The worklist snapshot is shared/cached globally (the scheduler builds it
    # without a user filter). Post-filter it to the requesting user's host scope
    # so it matches every other bridge element (no Windows host on a Linux scope).
    if worklist and host_scope:
        _scope_set = set(host_scope)
        worklist = dict(worklist)
        kept = [w for w in (worklist.get("items") or []) if (w.get("host") or "") in _scope_set]
        worklist["items"] = kept
        worklist["open_count"] = len(kept)

    # ── 6. Open Incidents (respect the user's CheckMK host scope) ───────────────
    open_incidents: list[dict] = []
    try:
        from sqlalchemy import select as _sel, desc as _desc, func as _func
        from app.models.workflow import Incident, IncidentMember
        _inc_q = (
            _sel(Incident)
            .where(Incident.status.in_(("open", "investigating")))
            .order_by(_desc(Incident.updated_at))
        )
        # When an OS/location filter is active, only show incidents whose host
        # is in scope (e.g. Linux-only → no Windows incidents on the bridge).
        if host_scope:
            _inc_q = _inc_q.where(Incident.primary_host.in_(host_scope))
        _inc_q = _inc_q.limit(5)
        inc_rows = await db.execute(_inc_q)
        for inc in inc_rows.scalars().all():
            cnt = await db.execute(
                _sel(_func.count()).where(IncidentMember.incident_id == inc.id)
            )
            open_incidents.append({
                "id": str(inc.id),
                "title": inc.title,
                "host": inc.primary_host,
                "severity": inc.severity,
                "status": inc.status,
                "member_count": cnt.scalar() or 0,
                "updated_at": inc.updated_at.isoformat(),
            })
    except Exception as e:
        log.debug("bridge: open_incidents failed: %s", e)

    # ── 6b. AI error-clusters (root-cause diagnoses) from the latest run ────────
    error_clusters: list[dict] = []
    try:
        from app.models.ai import AiAnalysis
        _ai_q = (
            _sel(AiAnalysis)
            .where(AiAnalysis.agent_type == "sysadmin")
            .order_by(_desc(AiAnalysis.run_at))
            .limit(1)
        )
        _ai_row = await db.execute(_ai_q)
        _latest = _ai_row.scalar_one_or_none()
        if _latest and _latest.clusters:
            _scope_set = set(host_scope) if host_scope else None

            def _in_scope(c: dict) -> bool:
                if not _scope_set:
                    return True
                hosts = list(c.get("affected_hosts") or [])
                if c.get("root_cause_host"):
                    hosts.append(c["root_cause_host"])
                return any(h in _scope_set for h in hosts)

            for c in _latest.clusters:
                if not _in_scope(c):
                    continue
                error_clusters.append({
                    "diagnosis": c.get("diagnosis", ""),
                    "severity": c.get("severity", "high"),
                    "root_cause_host": c.get("root_cause_host"),
                    "affected_hosts": c.get("affected_hosts") or [],
                    "recommendation": c.get("recommendation", ""),
                })
    except Exception as e:
        log.debug("bridge: error_clusters failed: %s", e)

    return {
        "alert_state": alert_state,
        "counts": {
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "total": total,
        },
        "sources": source_list,
        "sectors": sector_list,
        "primary_incident": primary_incident,
        "error_clusters": error_clusters,
        "logs": sensor_log,
        "vitals": vitals,
        "forecasts": forecasts,
        "worklist": worklist["items"] if worklist else [],
        "worklist_open_count": worklist["open_count"] if worklist else 0,
        "worklist_updated": worklist["created_at"] if worklist else None,
        "open_incidents": open_incidents,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/refresh-worklist")
async def refresh_worklist(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Rebuild the worklist on demand (otherwise runs on the configured interval)."""
    from app.services.worklist_builder import build_worklist
    from app.services.settings import get_agent_config
    cfg = await get_agent_config(db)
    snapshot = await build_worklist(db, hours=24, size=cfg.worklist_size, user_id=str(user.id))
    return {"ok": True, "count": len(snapshot.get("items", []))}
