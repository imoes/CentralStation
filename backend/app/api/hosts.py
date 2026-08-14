"""Host health endpoint — live CheckMK vitals + recent alerts for the Server Cockpit."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.services import feed_index

log = logging.getLogger(__name__)

router = APIRouter(prefix="/hosts", tags=["hosts"])

# Reuse bridge.py label/unit/level mapping + the CheckMK service each metric lives in
# (mirrors checkmk_metrics.STANDARD_METRICS).
_VITAL_METRICS = {
    "fs_used_percent":  {"label": "Disk", "unit": "%", "service": "Filesystem /"},
    "mem_used_percent": {"label": "RAM",  "unit": "%", "service": "Memory"},
    "load1":            {"label": "CPU",  "unit": "",  "service": "CPU load"},
}

# Display order for the cockpit
_VITAL_ORDER = ["load1", "mem_used_percent", "fs_used_percent"]


def _level(metric: str, value: float) -> str:
    """Map metric value to severity level (crit/high/ok)."""
    pct = (value / 8.0 * 100.0) if metric == "load1" else value
    if pct >= 90:
        return "crit"
    if pct >= 75:
        return "high"
    return "ok"


async def _get_checkmk_connector(db: AsyncSession):
    """Resolve an enabled CheckMK connector, or None. Shared by the live paths."""
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig as ConnectorModel
    from app.services.connectors import get_connector
    from sqlalchemy import select as sa_select

    result = await db.execute(
        sa_select(ConnectorModel)
        .where(ConnectorModel.type == "checkmk")
        .where(ConnectorModel.enabled == True)  # noqa: E712
        .limit(1)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        return None
    credentials = decrypt_credentials(conn.encrypted_credentials)
    return get_connector("checkmk", conn.base_url, credentials)


@router.get("/{hostname}/health")
async def host_health(
    hostname: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    live: bool = True,
):
    """Return health vitals + recent alerts for a host.

    Values come straight from CheckMK's RRD — there is no metric cache any more. The
    old one only sampled hosts that currently had a critical/high alert, so an
    unremarkable host had no vitals at all, and its 5-minute samples were coarser
    than the 1-minute resolution CheckMK returns for short ranges.

    `live_ok` reports whether CheckMK actually answered. When it did not, `vitals` is
    empty and `live_ok` is false — an explicitly empty result rather than stale
    numbers presented as current.
    """
    # ── 1. Vitals — live from CheckMK ───────────────────────────────────────
    # There is no metric cache any more: it only held hosts with an active
    # critical/high alert (so an unremarkable host showed an empty panel) and its
    # 5-minute samples were coarser than CheckMK's 1-minute resolution here.
    vitals: list[dict] = []
    live_ok = False
    if live:
        targets = [
            {
                "metric": metric,
                "label": _VITAL_METRICS[metric]["label"],
                "unit": _VITAL_METRICS[metric]["unit"],
                "service": _VITAL_METRICS[metric]["service"],
            }
            for metric in _VITAL_ORDER
        ]

        try:
            connector = await _get_checkmk_connector(db)
            if connector:
                import asyncio

                async def _refresh_vital(v: dict) -> dict | None:
                    service = v.get("service") or _VITAL_METRICS.get(v["metric"], {}).get("service", "")
                    try:
                        result = await connector.get_graph_data(
                            hostname, service, 0, 2, metric_id=v["metric"]
                        )
                        series = result.get("series", [])
                        if series:
                            current_val = round(float(series[-1]["value"]), 1)
                            return {
                                **v,
                                "service": service,
                                "value": current_val,
                                "level": _level(v["metric"], current_val),
                                "series": [{"time": p["time"], "value": p["value"]} for p in series[-30:]],
                                "source": "live",
                            }
                    except Exception as e:
                        log.debug("host_health live refresh %s/%s: %s", hostname, v["metric"], e)
                    # No live data for this metric → omit it rather than showing a
                    # placeholder value that looks like a reading.
                    return None

                refreshed = await asyncio.gather(*[_refresh_vital(v) for v in targets])
                live_vitals = [v for v in refreshed if v is not None]
                # Preserve display order
                order = {m: i for i, m in enumerate(_VITAL_ORDER)}
                live_vitals.sort(key=lambda v: order.get(v["metric"], 99))
                if live_vitals:
                    vitals = live_vitals
                # live_ok only when EVERY returned vital actually came from CheckMK —
                # a partial refresh must not be reported as a live reading.
                live_ok = bool(live_vitals) and all(
                    v.get("source") == "live" for v in live_vitals
                )
        except Exception as e:
            log.warning("host_health live refresh failed for %s: %s", hostname, e)

    # ── 3. Recent messages ──────────────────────────────────────────────────
    messages: list[dict] = []
    try:
        items = await feed_index.search(
            host=hostname,
            size=20,
            exclude_resolved=True,
            user_id=str(current_user.id),
            db=db,
        )
        messages = [
            {
                "id": item.get("id", ""),
                "external_id": item.get("external_id", ""),
                "severity": item.get("severity", "info"),
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "created_at": item.get("created_at", ""),
                "ai_insight": item.get("ai_insight", ""),
            }
            for item in (items or [])
        ]
    except Exception as e:
        log.warning("host_health messages for %s: %s", hostname, e)

    return {
        "host": hostname,
        "vitals": vitals,
        "messages": messages,
        "live": live,          # what was requested
        "live_ok": live_ok,    # what was actually delivered
    }


# Sort priority: CRIT first, then WARN, UNKNOWN, OK
_STATE_SORT = {2: 0, 1: 1, 3: 2, 0: 3}

_SEV_SORT = {"critical": 0, "warning": 1, "unknown": 2}


def _group_problems(problems: list[dict]) -> dict:
    """Group flat problem list into domain → host → services tree, sorted by CRIT count."""
    domains: dict[str, dict] = {}
    for p in problems:
        host = p.get("host", "")
        domain = host.split(".", 1)[1] if "." in host else host
        host_data = domains.setdefault(domain, {}).setdefault(host, {
            "host": host,
            "address": p.get("host_address", ""),
            "services": [],
            "counts": {"crit": 0, "warn": 0, "unknown": 0, "total": 0},
        })
        severity = p.get("severity", "unknown")
        host_data["services"].append({
            "host": host,
            "service": p.get("service", ""),
            "severity": severity,
            "output": p.get("output", ""),
            "last_state_change": p.get("last_state_change"),
            "host_address": p.get("host_address", ""),
        })
        if severity == "critical":
            host_data["counts"]["crit"] += 1
        elif severity == "warning":
            host_data["counts"]["warn"] += 1
        else:
            host_data["counts"]["unknown"] += 1
        host_data["counts"]["total"] += 1

    result_domains = []
    for domain, hosts in domains.items():
        domain_counts: dict = {"crit": 0, "warn": 0, "unknown": 0, "total": 0}
        host_list = []
        for host_data in hosts.values():
            host_data["services"].sort(key=lambda s: _SEV_SORT.get(s["severity"], 3))
            host_list.append(host_data)
            for k in domain_counts:
                domain_counts[k] += host_data["counts"][k]
        host_list.sort(key=lambda h: (-h["counts"]["crit"], -h["counts"]["total"]))
        result_domains.append({
            "domain": domain,
            "hosts": host_list,
            "counts": domain_counts,
            "host_count": len(host_list),
        })
    result_domains.sort(key=lambda d: (-d["counts"]["crit"], -d["counts"]["total"]))

    total_counts: dict = {"crit": 0, "warn": 0, "unknown": 0, "total": 0}
    for d in result_domains:
        for k in total_counts:
            total_counts[k] += d["counts"][k]

    return {
        "domains": result_domains,
        "counts": total_counts,
        "host_count": sum(d["host_count"] for d in result_domains),
    }


@router.get("/{hostname}/services")
async def host_services(
    hostname: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return all CheckMK services for a host (name, state, summary) + counts.

    One cheap CheckMK call gives the full health picture across every check.
    Empty list when the host is not monitored by CheckMK.
    """
    counts = {"crit": 0, "warn": 0, "unknown": 0, "ok": 0, "total": 0}
    services: list[dict] = []
    try:
        connector = await _get_checkmk_connector(db)
        if connector:
            raw = await connector.list_services(hostname)
            for svc in raw:
                state = svc.get("state", 0)
                if state == 2:
                    counts["crit"] += 1
                elif state == 1:
                    counts["warn"] += 1
                elif state == 3:
                    counts["unknown"] += 1
                else:
                    counts["ok"] += 1
            counts["total"] = len(raw)
            # CRIT → WARN → UNKNOWN → OK, then alphabetical
            services = sorted(
                raw,
                key=lambda s: (_STATE_SORT.get(s.get("state", 0), 9), s.get("name", "").lower()),
            )
    except Exception as e:
        log.warning("host_services for %s: %s", hostname, e)

    return {"host": hostname, "services": services, "counts": counts}


@router.get("/service-problems")
async def service_problems(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Live unhandled CheckMK service problems, grouped Domain → Host → Service.

    Applies the requesting user's CheckMK scope filters (location, ve, criticality, os)
    so the view respects the same visibility rules as the Alert Feed and Worklist.
    """
    conn = await _get_checkmk_connector(db)
    if not conn:
        return {"domains": [], "counts": {"crit": 0, "warn": 0, "unknown": 0, "total": 0}, "host_count": 0}

    try:
        problems = await conn.get_problems(include_unknown=True)
    except Exception as e:
        log.warning("service_problems: CheckMK query failed: %s", e)
        return {"domains": [], "counts": {"crit": 0, "warn": 0, "unknown": 0, "total": 0}, "host_count": 0}

    # Apply the user's CheckMK scope (same filters as feed / worklist)
    try:
        from sqlalchemy import select as _select
        from app.models.workflow import UserPreference
        r = await db.execute(_select(UserPreference).where(UserPreference.user_id == current_user.id))
        prefs = r.scalar_one_or_none()
    except Exception:
        prefs = None

    if prefs:
        def _to_set(v: list | None) -> set[str] | None:
            return {x.lower() for x in v if x} if v else None

        pref_loc  = _to_set(prefs.checkmk_locations)
        pref_ve   = _to_set(prefs.checkmk_ve)
        pref_crit = _to_set(prefs.checkmk_criticality)
        pref_os   = _to_set(prefs.checkmk_os)

        if any([pref_loc, pref_ve, pref_crit, pref_os]):
            filtered = []
            for p in problems:
                meta = p.get("metadata") or {}
                loc  = (meta.get("location")    or "").lower()
                ve   = (meta.get("ve")           or "").lower()
                crit = (meta.get("criticality")  or "").lower()
                os_v = (meta.get("os")           or "").lower()
                if pref_loc  and loc  and not any(f in loc  for f in pref_loc):  continue
                if pref_ve   and ve   and not any(f in ve   for f in pref_ve):   continue
                if pref_crit and crit and not any(f in crit for f in pref_crit): continue
                if pref_os   and os_v and not any(f in os_v for f in pref_os):   continue
                filtered.append(p)
            problems = filtered

    return _group_problems(problems)


@router.get("/{hostname}/graph")
async def host_service_graph(
    hostname: str,
    service: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    metric: str = "",
    hours: int = 24,
):
    """Return a time series for a single host service metric (on-demand graph).

    `metric` is optional — when empty, CheckMK resolves the service's first metric.
    """
    try:
        connector = await _get_checkmk_connector(db)
        if not connector:
            return {"series": [], "error": "No CheckMK connector configured"}
        result = await connector.get_graph_data(
            hostname, service, 0, hours, metric_id=metric
        )
        return {
            "series": result.get("series", []),
            "title": result.get("title", service),
            "unit": result.get("unit", ""),
            "error": result.get("error", ""),
        }
    except Exception as e:
        log.warning("host_service_graph %s/%s: %s", hostname, service, e)
        return {"series": [], "error": str(e)}
