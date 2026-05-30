"""CheckMK Metrics Collector — periodically writes RRD samples to cs-metrics-checkmk.

Every 5 minutes the scheduler calls collect_checkmk_metrics():
  1. Find hosts with active critical/high alerts in cs-feed-checkmk
  2. For each host fetch the configured standard services (CPU, Memory, Disk)
  3. Write the latest data point per metric to cs-metrics-checkmk

The KI agent's rag_lookup node uses query_metrics_for_host() to retrieve
recent metric context when analysing a critical alert.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Standard services + metric IDs to collect per host.
# Extend this list to add more metrics.
_DEFAULT_METRICS: list[dict] = [
    {"service": "CPU load",         "metric_id": "load1",          "unit": ""},
    {"service": "CPU load",         "metric_id": "load5",          "unit": ""},
    {"service": "Memory",           "metric_id": "mem_used_percent","unit": "%"},
    {"service": "Memory",           "metric_id": "mem_used",       "unit": "bytes"},
    {"service": "Filesystem /",     "metric_id": "fs_used_percent", "unit": "%"},
    {"service": "Check_MK",         "metric_id": "cmk_time_agent", "unit": "s"},
]

# How many hours of RRD history to fetch for the latest data point
_FETCH_HOURS = 1
# Maximum hosts to collect metrics for per run (avoid overloading CheckMK)
_MAX_HOSTS = 20


async def collect_checkmk_metrics() -> int:
    """Fetch metrics from CheckMK for recently active hosts and store in OpenSearch.

    Returns the number of metric data points written.
    """
    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.checkmk import CheckMKConnector
    from app.services.feed_index import METRICS_INDEX
    from sqlalchemy import select

    written = 0
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.type == "checkmk",
                    ConnectorConfig.enabled.is_(True),
                ).limit(1)
            )
            conn = result.scalars().first()
            if not conn:
                return 0
            creds = decrypt_credentials(conn.encrypted_credentials)

        connector = CheckMKConnector(base_url=conn.base_url, credentials=creds)
        hosts = await _get_active_hosts(connector)
        if not hosts:
            log.debug("metrics_collector: no active hosts found")
            return 0

        os_client = get_opensearch()
        now = datetime.now(timezone.utc)

        for host in hosts[:_MAX_HOSTS]:
            for metric_cfg in _DEFAULT_METRICS:
                try:
                    data = await connector.get_graph_data(
                        host_name=host,
                        service_description=metric_cfg["service"],
                        metric_id=metric_cfg["metric_id"],
                        hours=_FETCH_HOURS,
                    )
                    series = data.get("series", [])
                    if not series:
                        continue
                    # Use the most recent non-null point
                    latest = next((p for p in reversed(series) if p["value"] is not None), None)
                    if latest is None:
                        continue

                    doc = {
                        "host":      host,
                        "service":   metric_cfg["service"],
                        "metric":    metric_cfg["metric_id"],
                        "value":     latest["value"],
                        "unit":      metric_cfg.get("unit", ""),
                        "timestamp": latest["time"],
                    }
                    doc_id = f"{host}_{metric_cfg['metric_id']}_{latest['time']}"
                    await os_client.index(
                        index=METRICS_INDEX,
                        id=doc_id,
                        body=doc,
                    )
                    written += 1
                except Exception as e:
                    log.debug("metrics_collector: %s/%s failed: %s", host, metric_cfg["metric_id"], e)

    except Exception as e:
        log.warning("metrics_collector: collection failed: %s", e)

    if written:
        log.info("metrics_collector: wrote %d metric points", written)
    return written


async def _get_active_hosts(connector: "CheckMKConnector") -> list[str]:
    """Return hostnames with active WARN/CRIT services in CheckMK."""
    try:
        problems = await connector.get_problems(time_range_minutes=120)
        hosts = list({p.get("host", "") for p in problems if p.get("host")})
        return [h for h in hosts if h]
    except Exception as e:
        log.debug("metrics_collector: could not get active hosts: %s", e)
        return []


async def query_metrics_for_host(host: str, hours: int = 2) -> list[dict]:
    """Return recent metric data points for a host from cs-metrics-checkmk.

    Used by the KI agent's rag_lookup node to add metric context to analysis.
    Returns list of {host, service, metric, value, unit, timestamp}.
    """
    from app.core.opensearch import get_opensearch
    from app.services.feed_index import METRICS_INDEX

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        os_client = get_opensearch()
        resp = await os_client.search(
            index=METRICS_INDEX,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"host": host}},
                            {"range": {"timestamp": {"gte": since}}},
                        ]
                    }
                },
                "sort": [{"timestamp": {"order": "desc"}}],
                "size": 50,
            },
            ignore_unavailable=True,
        )
        return [hit["_source"] for hit in resp.get("hits", {}).get("hits", [])]
    except Exception as e:
        log.debug("query_metrics_for_host(%s): %s", host, e)
        return []
