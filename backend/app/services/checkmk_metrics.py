"""Live host vitals from CheckMK's RRD — the single place that fetches them.

This replaces the cs-metrics-checkmk OpenSearch cache. That cache was written every
5 minutes, but only for hosts that had an ACTIVE critical/high alert (max 20 per
run), so an unremarkable host simply had no vitals — the cockpit showed an empty
panel and the AI agent had to special-case "no metrics stored, which is normal".
It was also coarser than the source: CheckMK returns 1-minute resolution for short
ranges against the collector's 5-minute samples, and no caller ever read it beyond
hours=2, so its depth bought nothing.

Reading live removes the second source of truth. The cost is one round-trip per
host, which is why the per-metric requests run concurrently (serially they took
~2.4s; concurrently ~1.2s).

Standard vitals are defined by STANDARD_METRICS below.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Services + metric IDs fetched as a host's standard vitals.
STANDARD_METRICS: list[dict] = [
    {"service": "CPU load",     "metric_id": "load1",           "unit": ""},
    {"service": "CPU load",     "metric_id": "load5",           "unit": ""},
    {"service": "Memory",       "metric_id": "mem_used_percent", "unit": "%"},
    {"service": "Memory",       "metric_id": "mem_used",        "unit": "bytes"},
    {"service": "Filesystem /", "metric_id": "fs_used_percent",  "unit": "%"},
    {"service": "Check_MK",     "metric_id": "cmk_time_agent",  "unit": "s"},
]


def trend(series: list[dict]) -> tuple[float | None, float | None, float | None, str]:
    """Return (current, min, max, trend_arrow) from an RRD time series."""
    vals = [p["value"] for p in series if p.get("value") is not None]
    if not vals:
        return None, None, None, "?"
    current = vals[-1]
    mn, mx = min(vals), max(vals)
    mid = len(vals) // 2 or 1
    avg_first = sum(vals[:mid]) / mid
    avg_last = sum(vals[mid:]) / max(len(vals[mid:]), 1)
    if avg_last > avg_first * 1.07:
        arrow = "↑"
    elif avg_last < avg_first * 0.93:
        arrow = "↓"
    else:
        arrow = "→"
    return current, mn, mx, arrow


async def checkmk_configs(db: Any = None) -> list[tuple[Any, dict]]:
    """Return all enabled CheckMK connector configs + decrypted credentials."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials

    async def _load(session) -> list[tuple[Any, dict]]:
        cfgs = (await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "checkmk",
                ConnectorConfig.enabled.is_(True),
            )
        )).scalars().all()
        return [(c, decrypt_credentials(c.encrypted_credentials)) for c in cfgs]

    if db is not None:
        return await _load(db)
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await _load(session)


async def fetch_host_metrics(hostname: str, hours: int = 2, db: Any = None) -> dict:
    """Fetch a host's standard vitals live from CheckMK.

    Tries every enabled site until the host is found. Returns
    {hostname, site, hours, metrics:[{service, metric, current, min, max, trend, unit}]}
    or {hostname, error} — the error is named so callers can say WHY there are no
    values instead of rendering an unexplained empty panel.
    """
    from app.services.connectors.checkmk import CheckMKConnector

    configs = await checkmk_configs(db)
    if not configs:
        return {"hostname": hostname, "error": "Kein CheckMK-Connector konfiguriert"}

    for cfg, creds in configs:
        connector = CheckMKConnector(base_url=cfg.base_url, credentials=creds)
        try:
            services = await connector.list_services(hostname)
        except Exception:
            services = []
        if not services:
            continue

        # Independent RRD round-trips → run them concurrently.
        async def _one(m: dict) -> tuple[dict, dict]:
            try:
                return m, await connector.get_graph_data(
                    hostname, m["service"], metric_id=m["metric_id"], hours=hours
                )
            except Exception as exc:  # one bad metric must not sink the host
                log.debug("get_graph_data %s/%s: %s", hostname, m["metric_id"], exc)
                return m, {}

        metrics_out: list[dict] = []
        for m, data in await asyncio.gather(*(_one(m) for m in STANDARD_METRICS)):
            series = data.get("series", [])
            if not series:
                continue
            cur, mn, mx, arrow = trend(series)
            unit = m.get("unit", "")
            if unit == "bytes" and cur is not None:
                cur, mn, mx = cur / 1e9, mn / 1e9, mx / 1e9
                unit = "GB"
            metrics_out.append({
                "service": m["service"],
                "metric":  m["metric_id"],
                "current": round(cur, 2) if cur is not None else None,
                "min":     round(mn, 2) if mn is not None else None,
                "max":     round(mx, 2) if mx is not None else None,
                "trend":   arrow,
                "unit":    unit,
            })
        return {"hostname": hostname, "site": cfg.name, "hours": hours,
                "metrics": metrics_out}

    return {"hostname": hostname, "error": "Host auf keinem CheckMK-Standort gefunden"}
