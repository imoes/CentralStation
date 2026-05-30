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
                        "must_not": [{"term": {"status": "resolved"}}],
                    }
                },
                "size": 0,
                "aggs": {
                    "by_sev": {"terms": {"field": "severity", "size": 10}},
                    "by_source": {
                        "terms": {"field": "source", "size": 10},
                        "aggs": {"sev": {"terms": {"field": "severity", "size": 10}}},
                    },
                    "by_location": {
                        "terms": {"field": "location_name", "size": 30},
                        "aggs": {"sev": {"terms": {"field": "severity", "size": 10}}},
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

        for b in aggs.get("by_location", {}).get("buckets", []):
            loc = b["key"]
            if not loc:
                continue
            crit = high = 0
            for sev in b.get("sev", {}).get("buckets", []):
                if sev["key"] == "critical":
                    crit = sev["doc_count"]
                elif sev["key"] == "high":
                    high = sev["doc_count"]
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
                        "must_not": [{"term": {"status": "resolved"}}],
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
                            "must_not": [{"term": {"status": "resolved"}}],
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

    # ── 4. AI-prioritised worklist (cached snapshot, no LLM at request time) ──
    from app.services.worklist_builder import get_latest_worklist
    worklist = await get_latest_worklist(db)

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
        "logs": sensor_log,
        "worklist": worklist["items"] if worklist else [],
        "worklist_open_count": worklist["open_count"] if worklist else 0,
        "worklist_updated": worklist["created_at"] if worklist else None,
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
    snapshot = await build_worklist(db, hours=24, size=cfg.worklist_size)
    return {"ok": True, "count": len(snapshot.get("items", []))}
