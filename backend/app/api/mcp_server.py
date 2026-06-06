"""CentralStation FastMCP Server — exposes IT-Ops tools for Hermes.

Hermes configures this as an MCP server and can call these tools to query
alerts, check hosts, search logs, acknowledge alerts, and create tickets.

Mounted at /api/mcp in main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastmcp import FastMCP

log = logging.getLogger(__name__)

mcp = FastMCP("CentralStation IT-Ops", version="1.0.0")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _get_os():
    from app.core.opensearch import get_opensearch
    return await get_opensearch()


# ── Tool 1: Bridge Status ──────────────────────────────────────────

@mcp.tool()
async def get_bridge_status() -> dict:
    """Gibt den aktuellen IT-Systemstatus zurück: Alert-Schweregrade, offene Incidents,
    Fleet-Vitals (Disk/RAM/CPU) und aktive Probleme pro Quelle (CheckMK, Graylog, Wazuh).
    Nutze dieses Tool für allgemeine Statusabfragen wie 'Wie ist der aktuelle Stand?'"""
    from sqlalchemy import select, func
    from app.models.alert import Alert

    async with (await _get_db_session()) as db:
        sources = ["checkmk", "graylog", "wazuh"]
        severities = ["critical", "high", "medium", "low", "info"]

        # Count alerts by source + severity (last 6h)
        since = _now_utc() - timedelta(hours=6)
        result = await db.execute(
            select(Alert.source, Alert.severity, func.count())
            .where(Alert.status != "resolved", Alert.created_at >= since)
            .group_by(Alert.source, Alert.severity)
        )
        rows = result.all()

        src_counts: dict[str, dict] = {s: {sev: 0 for sev in severities} for s in sources}
        for source, severity, cnt in rows:
            if source in src_counts and severity in src_counts[source]:
                src_counts[source][severity] += cnt

        total_crit = sum(src_counts[s]["critical"] for s in sources)
        total_high = sum(src_counts[s]["high"] for s in sources)
        total_med  = sum(src_counts[s]["medium"] for s in sources)
        total      = sum(sum(src_counts[s].values()) for s in sources)

        state = "red" if total_crit > 0 else ("yellow" if total_high > 0 else "green")

        return {
            "alert_state": state,
            "counts": {"critical": total_crit, "high": total_high, "medium": total_med, "total": total},
            "sources": [
                {
                    "name": s,
                    "critical": src_counts[s]["critical"],
                    "high": src_counts[s]["high"],
                    "total": sum(src_counts[s].values()),
                }
                for s in sources
            ],
            "timestamp": _now_utc().isoformat(),
        }


# ── Tool 2: List Alerts ────────────────────────────────────────────

@mcp.tool()
async def list_alerts(
    hours: int = 6,
    severity: str = "",
    source: str = "",
    limit: int = 20,
) -> list[dict]:
    """Listet aktive Alerts aus CheckMK, Graylog und Wazuh.

    Parameter:
    - hours: Zeitraum in Stunden (1-72, Standard 6)
    - severity: Filter nach Schweregrad (critical/high/medium/low/info, leer = alle)
    - source: Filter nach Quelle (checkmk/graylog/wazuh, leer = alle)
    - limit: Maximale Anzahl Ergebnisse (max 50)

    Nutze dieses Tool wenn der Nutzer nach aktuellen Problemen oder Alerts fragt."""
    from sqlalchemy import select
    from app.models.alert import Alert

    hours = max(1, min(72, hours))
    limit = max(1, min(50, limit))
    since = _now_utc() - timedelta(hours=hours)

    stmt = select(Alert).where(Alert.status != "resolved", Alert.created_at >= since)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if source:
        stmt = stmt.where(Alert.source == source)
    stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)

    async with (await _get_db_session()) as db:
        rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": str(a.id),
            "source": a.source,
            "severity": a.severity,
            "title": a.title,
            "host": a.metadata_.get("host", "") if a.metadata_ else "",
            "location": a.location_name or "",
            "status": a.status,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


# ── Tool 3: Search Feed ────────────────────────────────────────────

@mcp.tool()
async def search_feed(query: str, index: str = "cs-feed-*", limit: int = 10) -> list[dict]:
    """Durchsucht den OpenSearch-Alert-Feed mit Lucene-Syntax.

    Parameter:
    - query: Lucene-Abfrage (z.B. 'severity:critical AND host:docker*')
    - index: OpenSearch-Index (Standard: cs-feed-*, oder cs-feed-checkmk, cs-feed-graylog, cs-feed-wazuh)
    - limit: Maximale Ergebnisse (max 30)

    Beispiele:
    - 'host:docker086'
    - 'severity:critical AND source:checkmk'
    - 'body:*disk*'

    Nutze dieses Tool für spezifische Log-Suchen."""
    limit = max(1, min(30, limit))
    os_client = await _get_os()
    try:
        resp = await os_client.search(
            index=index,
            body={
                "query": {"query_string": {"query": query, "default_field": "body"}},
                "size": limit,
                "sort": [{"created_at": {"order": "desc"}}],
            },
            ignore_unavailable=True,
        )
        hits = resp.get("hits", {}).get("hits", [])
        return [
            {
                "id": h.get("_id", ""),
                "source": h["_source"].get("source", ""),
                "severity": h["_source"].get("severity", ""),
                "title": h["_source"].get("title", ""),
                "body": (h["_source"].get("body", "") or "")[:300],
                "host": h["_source"].get("host", ""),
                "created_at": h["_source"].get("created_at", ""),
            }
            for h in hits
        ]
    except Exception as exc:
        log.warning("search_feed error: %s", exc)
        return []


# ── Tool 4: Acknowledge Alert ──────────────────────────────────────

@mcp.tool()
async def acknowledge_alert(alert_id: str) -> dict:
    """Bestätigt einen Alert (setzt Status auf 'acknowledged').

    Parameter:
    - alert_id: UUID des Alerts (aus list_alerts)

    Nutze dieses Tool wenn der Nutzer einen Alert quittieren/bestätigen möchte."""
    import uuid as uuid_mod
    from sqlalchemy import select
    from app.models.alert import Alert

    try:
        uid = uuid_mod.UUID(alert_id)
    except ValueError:
        return {"ok": False, "error": "Ungültige Alert-ID"}

    async with (await _get_db_session()) as db:
        row = (await db.execute(select(Alert).where(Alert.id == uid))).scalar_one_or_none()
        if not row:
            return {"ok": False, "error": "Alert nicht gefunden"}
        row.status = "acknowledged"
        await db.commit()
    return {"ok": True, "alert_id": alert_id, "new_status": "acknowledged"}


# ── Tool 5: Get CheckMK Host ───────────────────────────────────────

@mcp.tool()
async def get_checkmk_host(hostname: str) -> dict:
    """Ruft den CheckMK-Status eines Hosts ab: alle Services, offene Probleme, Metriken.

    Parameter:
    - hostname: Hostname oder FQDN (z.B. 'docker086' oder 'docker086.ippen.media')

    Nutze dieses Tool wenn der Nutzer den Status eines bestimmten Servers wissen will."""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.services.connectors.checkmk import CheckMKConnector
    from app.core.security import decrypt_credentials

    async with (await _get_db_session()) as db:
        configs = (await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "checkmk",
                ConnectorConfig.enabled.is_(True),
            )
        )).scalars().all()

    if not configs:
        return {"error": "Kein CheckMK-Connector konfiguriert"}

    # Multi-location: try every enabled CheckMK site until the host is found.
    errors: list[str] = []
    for cfg in configs:
        creds = decrypt_credentials(cfg.encrypted_credentials)
        connector = CheckMKConnector(base_url=cfg.base_url, credentials=creds)
        try:
            services = await connector.list_services(hostname)
            if services:
                return {"hostname": hostname, "site": cfg.name, "services": services}
        except Exception as exc:
            log.warning("get_checkmk_host %s on '%s': %s", hostname, cfg.name, exc)
            errors.append(f"{cfg.name}: {exc}")

    return {
        "hostname": hostname,
        "error": "Host auf keinem CheckMK-Standort gefunden",
        "details": errors or None,
    }


# ── Tool 6: Create Jira Ticket ─────────────────────────────────────

@mcp.tool()
async def create_jira_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Erstellt ein Jira-Ticket im Standard-Projekt.

    Parameter:
    - title: Kurze Zusammenfassung des Problems (max 200 Zeichen)
    - description: Ausführliche Beschreibung
    - priority: Priorität (critical/high/medium/low, Standard: medium)

    Nutze dieses Tool wenn der Nutzer ein Ticket oder eine Aufgabe erstellen möchte."""
    import json as _json
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.services.connectors.jira import JiraConnector
    from app.models.settings import GlobalSetting
    from app.core.security import decrypt_credentials

    async with (await _get_db_session()) as db:
        cfg_result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type.in_(["jira", "jira_sd"]),
                ConnectorConfig.enabled.is_(True),
            ).limit(1)
        )
        cfg = cfg_result.scalar_one_or_none()
        if not cfg:
            return {"ok": False, "error": "Kein Jira-Connector konfiguriert"}

        # Default project from global settings
        proj_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.default_project")
        )).scalar_one_or_none()
        project = proj_row.value_plain if proj_row else "IMIT"

        # Priority names are instance-specific. Default to the standard Jira
        # priority names (Highest/High/Medium/Low); override per instance via the
        # global setting jira.priority_map = {"critical": "Kritisch", ...}.
        prio_map = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
        map_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.priority_map")
        )).scalar_one_or_none()
        if map_row and map_row.value_plain:
            try:
                prio_map.update(_json.loads(map_row.value_plain))
            except Exception:
                log.warning("jira.priority_map is not valid JSON, using defaults")
        jira_priority = prio_map.get(priority, prio_map.get("medium", ""))

        creds = decrypt_credentials(cfg.encrypted_credentials)
        connector = JiraConnector(base_url=cfg.base_url, credentials=creds)
        try:
            issue = await connector.create_issue(
                project=project,
                summary=title[:200],
                description=description,
                priority=jira_priority,
            )
            return {"ok": True, "jira_key": issue.get("key", ""), "url": issue.get("url", "")}
        except Exception as exc:
            # A wrong/unknown priority name rejects the whole create → retry once
            # without priority so the ticket still gets created.
            log.warning("create_jira_ticket failed (%s) — retrying without priority", exc)
            try:
                issue = await connector.create_issue(
                    project=project,
                    summary=title[:200],
                    description=description,
                    priority="",
                )
                return {"ok": True, "jira_key": issue.get("key", ""),
                        "url": issue.get("url", ""), "note": "ohne Priorität erstellt"}
            except Exception as exc2:
                log.warning("create_jira_ticket retry failed: %s", exc2)
                return {"ok": False, "error": str(exc2)}


# ── DB session helper ──────────────────────────────────────────────

async def _get_db_session():
    """Returns an async context manager for a DB session."""
    from app.core.database import AsyncSessionLocal
    return AsyncSessionLocal()
