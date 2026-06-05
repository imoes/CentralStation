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

# Reuse bridge.py label/unit/level mapping
_VITAL_METRICS = {
    "fs_used_percent":  {"label": "Disk", "unit": "%"},
    "mem_used_percent": {"label": "RAM",  "unit": "%"},
    "load1":            {"label": "CPU",  "unit": ""},
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
    if live and vitals:
        try:
            from app.core.security import decrypt_credentials
            from app.models.connector import ConnectorConfig as ConnectorModel
            from app.services.connectors import get_connector
            from sqlalchemy import select as sa_select

            cmk_result = await db.execute(
                sa_select(ConnectorModel)
                .where(ConnectorModel.type == "checkmk")
                .where(ConnectorModel.enabled == True)  # noqa: E712
                .limit(1)
            )
            cmk_conn = cmk_result.scalar_one_or_none()
            if cmk_conn:
                credentials = decrypt_credentials(cmk_conn.encrypted_credentials)
                connector = get_connector("checkmk", cmk_conn.base_url, credentials)
                import asyncio

                async def _refresh_vital(v: dict) -> dict:
                    try:
                        result = await connector.get_graph_data(
                            hostname, v["service"], 0, 2, metric_id=v["metric"]
                        )
                        series = result.get("series", [])
                        if series:
                            current_val = round(float(series[-1]["value"]), 1)
                            return {
                                **v,
                                "value": current_val,
                                "level": _level(v["metric"], current_val),
                                "series": [{"time": p["time"], "value": p["value"]} for p in series[-30:]],
                            }
                    except Exception as e:
                        log.debug("host_health live refresh %s/%s: %s", hostname, v["metric"], e)
                    return v

                vitals = list(await asyncio.gather(*[_refresh_vital(v) for v in vitals]))
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
