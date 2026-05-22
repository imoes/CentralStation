"""Alert aggregation service.

Polls all enabled connectors (CheckMK, Graylog, Wazuh) and stores new alerts
in the alerts table. Deduplicates via external_id.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_credentials
from app.models.alert import Alert
from app.models.connector import ConnectorConfig

log = logging.getLogger(__name__)

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


async def collect_checkmk(connector: ConnectorConfig) -> list[dict]:
    from app.services.connectors.checkmk import CheckMKConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = CheckMKConnector(base_url=connector.base_url, credentials=creds)
    try:
        items = await svc.get_problems()
        return [
            {
                "source": "checkmk",
                "severity": i["severity"],
                "title": f"{i['host']} — {i['service']}",
                "body": i.get("output", ""),
                "external_id": f"cmk:{i['host']}:{i['service']}",
                "metadata": {
                    **(i.get("metadata") or {}),
                    "host": i["host"],
                    "service": i["service"],
                    "host_address": i.get("host_address", ""),
                },
            }
            for i in items
        ]
    except Exception as e:
        log.warning("CheckMK collection failed: %s", e)
        return []


async def collect_graylog(connector: ConnectorConfig) -> list[dict]:
    from app.services.connectors.graylog import GraylogConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = GraylogConnector(base_url=connector.base_url, credentials=creds)
    try:
        # Exclude switch events (handled by network agent)
        msgs = await svc.search_messages(
            query='level:<=4 AND NOT source:(nsa* OR nss* OR nsc*)',
            time_range_seconds=3600,
            limit=100,
        )
        severity_map = {0: "critical", 1: "critical", 2: "critical", 3: "critical",
                        4: "high", 5: "medium", 6: "low", 7: "info"}
        return [
            {
                "source": "graylog",
                "severity": severity_map.get(m.get("level", 6), "medium"),
                "title": m["message"][:200],
                "body": m["message"],
                "external_id": f"glog:{m['id']}",
            }
            for m in msgs
        ]
    except Exception as e:
        log.warning("Graylog collection failed: %s", e)
        return []


async def collect_wazuh(connector: ConnectorConfig) -> list[dict]:
    from app.services.connectors.wazuh import WazuhConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = WazuhConnector(base_url=connector.base_url, credentials=creds)
    try:
        items = await svc.get_alerts(limit=100)
        return [
            {
                "source": "wazuh",
                "severity": i["severity"],
                "title": i["title"],
                "body": i.get("body", ""),
                "external_id": f"wazuh:{i['external_id']}",
            }
            for i in items
        ]
    except Exception as e:
        log.warning("Wazuh collection failed: %s", e)
        return []


COLLECTORS = {
    "checkmk": collect_checkmk,
    "graylog": collect_graylog,
    "wazuh": collect_wazuh,
}


async def run_aggregation(db: AsyncSession) -> int:
    """Poll all enabled connectors, persist new alerts, return count of new alerts."""
    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type.in_(list(COLLECTORS.keys())),
            ConnectorConfig.enabled.is_(True),
        )
    )
    connectors = result.scalars().all()

    new_count = 0
    for connector in connectors:
        collector = COLLECTORS.get(connector.type)
        if not collector:
            continue
        items = await collector(connector)
        for item in items:
            ext_id = item.get("external_id")
            if ext_id:
                existing = await db.execute(
                    select(Alert).where(Alert.external_id == ext_id, Alert.status != "resolved")
                )
                if existing.scalar_one_or_none():
                    continue

            alert = Alert(
                source=item["source"],
                severity=item["severity"],
                title=item["title"][:512],
                body=item.get("body"),
                external_id=ext_id,
                status="new",
                metadata_=item.get("metadata"),
            )
            db.add(alert)
            new_count += 1

    if new_count > 0:
        await db.commit()
        log.info("Aggregated %d new alerts", new_count)

        # Index new alerts in OpenSearch (best-effort)
        try:
            from app.services.feed_index import index_items
            docs = [
                {
                    "id": str(a.id),
                    "type": "alert",
                    "source": a.source,
                    "severity": a.severity,
                    "title": a.title,
                    "body": a.body,
                    "metadata": a.metadata_,
                    "created_at": a.created_at.isoformat(),
                    "status": a.status,
                    "location_name": a.location_name,
                    "location_city": a.location_city,
                    "external_url": None,
                    "external_id": a.external_id,
                }
                for a in db.new
                if hasattr(a, "source")
            ]
            if docs:
                await index_items(docs)
        except Exception as exc:
            log.warning("OpenSearch indexing failed (non-fatal): %s", exc)

    return new_count
