"""Host health endpoint — serves cached metrics + live CheckMK refresh for the Server Cockpit."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.services import feed_index
from app.services.metrics_collector import query_metrics_for_host

log = logging.getLogger(__name__)

router = APIRouter(prefix="/hosts", tags=["hosts"])

# Reuse bridge.py label/unit/level mapping + CheckMK service per metric
# (service names mirror metrics_collector._STANDARD_METRICS so the live path
#  can query CheckMK even when the metrics cache is empty/stale).
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
    live: bool = False,
):
    """Return health vitals + recent alerts for a host.

    Default (live=false): returns cached metrics from cs-metrics-checkmk (instant).
    With live=true: additionally fetches current values from CheckMK RRD.
    """
    # ── 1. Vitals (cached) ──────────────────────────────────────────────────
    raw_docs = await query_metrics_for_host(hostname, hours=2)

    # Group by metric — keep series for sparklines, latest value for gauge
    series_by_metric: dict[str, list[dict]] = {}
    service_by_metric: dict[str, str] = {}
    unit_by_metric: dict[str, str] = {}
    for doc in reversed(raw_docs):  # oldest first → build series
        m = doc.get("metric", "")
        if m not in _VITAL_METRICS:
            continue
        if m not in series_by_metric:
            series_by_metric[m] = []
            service_by_metric[m] = doc.get("service", "")
            unit_by_metric[m] = doc.get("unit", "") or _VITAL_METRICS[m]["unit"]
        series_by_metric[m].append({"time": doc["timestamp"], "value": float(doc["value"])})

    vitals = []
    for metric in _VITAL_ORDER:
        if metric not in series_by_metric:
            continue
        series = series_by_metric[metric]
        current_val = round(series[-1]["value"], 1) if series else 0.0
        vitals.append({
            "metric": metric,
            "label": _VITAL_METRICS[metric]["label"],
            "value": current_val,
            "unit": unit_by_metric.get(metric, _VITAL_METRICS[metric]["unit"]),
            "level": _level(metric, current_val),
            "service": service_by_metric.get(metric, ""),
            "series": series[-30:],  # max 30 points for sparkline
        })

    # ── 2. Live refresh (optional) ──────────────────────────────────────────
    # When live=true we always query CheckMK — even if the cache is empty/stale.
    # Cached vitals are refreshed in place; if the cache is empty we build stubs
    # from the known metric→service map so the cockpit fills from CheckMK directly.
    if live:
        # Determine which vitals to query: cached ones, or fallback stubs.
        if vitals:
            targets = vitals
        else:
            targets = [
                {
                    "metric": metric,
                    "label": _VITAL_METRICS[metric]["label"],
                    "value": 0.0,
                    "unit": _VITAL_METRICS[metric]["unit"],
                    "level": "ok",
                    "service": _VITAL_METRICS[metric]["service"],
                    "series": [],
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
                            }
                    except Exception as e:
                        log.debug("host_health live refresh %s/%s: %s", hostname, v["metric"], e)
                    # No live data: keep cached vital if it had data, else drop the stub
                    return v if v.get("series") else None

                refreshed = await asyncio.gather(*[_refresh_vital(v) for v in targets])
                live_vitals = [v for v in refreshed if v is not None]
                # Preserve display order
                order = {m: i for i, m in enumerate(_VITAL_ORDER)}
                live_vitals.sort(key=lambda v: order.get(v["metric"], 99))
                if live_vitals:
                    vitals = live_vitals
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
        "live": live,
    }


# Sort priority: CRIT first, then WARN, UNKNOWN, OK
_STATE_SORT = {2: 0, 1: 1, 3: 2, 0: 3}


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
