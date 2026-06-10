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


def _get_os():
    from app.core.opensearch import get_opensearch
    return get_opensearch()  # sync singleton, no await


# ── Tool 1: Bridge Status ──────────────────────────────────────────

@mcp.tool()
async def get_bridge_status() -> dict:
    """Returns the current IT system status: alert severity counts, open incidents,
    fleet vitals (disk/RAM/CPU) and active problems per source (CheckMK, Graylog, Wazuh).
    Use this tool for general status queries like 'What is the current state?'"""
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
    """Lists active alerts from CheckMK, Graylog and Wazuh.

    Parameters:
    - hours: time window in hours (1-72, default 6)
    - severity: filter by severity (critical/high/medium/low/info, empty = all)
    - source: filter by source (checkmk/graylog/wazuh, empty = all)
    - limit: maximum number of results (max 50)

    Use this tool when the user asks about current problems or alerts."""
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
            "external_id": a.external_id or "",
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
    """Searches the OpenSearch alert feed using Lucene syntax.

    Parameters:
    - query: Lucene query (e.g. 'severity:critical AND host:docker*')
    - index: OpenSearch index (default: cs-feed-*, or cs-feed-checkmk, cs-feed-graylog, cs-feed-wazuh)
    - limit: maximum results (max 30)

    Examples:
    - 'host:docker086'
    - 'severity:critical AND source:checkmk'
    - 'body:*disk*'

    Use this tool for specific log searches."""
    limit = max(1, min(30, limit))
    os_client = _get_os()
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
                "external_id": h["_source"].get("external_id", ""),
                "source": h["_source"].get("source", ""),
                "severity": h["_source"].get("severity", ""),
                "title": h["_source"].get("title", ""),
                "body": (h["_source"].get("body", "") or "")[:300],
                "host": h["_source"].get("host", ""),
                "ai_insight": h["_source"].get("ai_insight", ""),
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
    """Acknowledges an alert (sets status to 'acknowledged').

    Parameters:
    - alert_id: UUID of the alert (from list_alerts)

    Use this tool when the user wants to acknowledge or confirm an alert."""
    import uuid as uuid_mod
    from sqlalchemy import select
    from app.models.alert import Alert

    try:
        uid = uuid_mod.UUID(alert_id)
    except ValueError:
        return {"ok": False, "error": "Invalid alert ID"}

    async with (await _get_db_session()) as db:
        row = (await db.execute(select(Alert).where(Alert.id == uid))).scalar_one_or_none()
        if not row:
            return {"ok": False, "error": "Alert not found"}
        row.status = "acknowledged"
        await db.commit()
    return {"ok": True, "alert_id": alert_id, "new_status": "acknowledged"}


# ── Tool 5: Get CheckMK Host ───────────────────────────────────────

@mcp.tool()
async def get_checkmk_host(hostname: str) -> dict:
    """Fetches the CheckMK status of a host: all services, open problems, metrics.

    Parameters:
    - hostname: hostname or FQDN (e.g. 'docker086' or 'docker086.example.com')

    Use this tool when the user wants to know the status of a specific server."""
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
        return {"error": "No CheckMK connector configured"}

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
        "error": "Host not found on any CheckMK site",
        "details": errors or None,
    }


# ── Tool 6: Create Jira Ticket ─────────────────────────────────────

@mcp.tool()
async def create_jira_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Creates a Jira ticket in the default project.

    Parameters:
    - title: short summary of the problem (max 200 characters)
    - description: detailed description
    - priority: priority (critical/high/medium/low, default: medium)

    Use this tool when the user wants to create a ticket or task."""
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
            return {"ok": False, "error": "No Jira connector configured"}

        # Default project from global settings
        proj_row = (await db.execute(
            select(GlobalSetting).where(GlobalSetting.key == "jira.default_project")
        )).scalar_one_or_none()
        project = proj_row.value_plain if proj_row else "OPS"

        # Priority names are instance-specific. Default to the standard Jira
        # priority names (Highest/High/Medium/Low); override per instance via the
        # global setting jira.priority_map = {"critical": "Highest", ...}.
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
                        "url": issue.get("url", ""), "note": "created without priority"}
            except Exception as exc2:
                log.warning("create_jira_ticket retry failed: %s", exc2)
                return {"ok": False, "error": str(exc2)}


# ── Tool 7: Get Alert Analysis ─────────────────────────────────────

@mcp.tool()
async def get_alert_analysis(external_id: str) -> dict:
    """Returns stored AI analyses and comments for an alert.

    Parameters:
    - external_id: the external ID of the alert (from list_alerts or search_feed)

    Use this tool when you want to know what has already been analysed or
    commented on an alert — e.g. for incident investigations."""
    from sqlalchemy import select
    from app.models.workflow import AlertComment

    async with (await _get_db_session()) as db:
        rows = (await db.execute(
            select(AlertComment)
            .where(AlertComment.external_id == external_id)
            .order_by(AlertComment.created_at.desc())
            .limit(20)
        )).scalars().all()

    return {
        "external_id": external_id,
        "comments": [
            {
                "kind": r.kind,
                "user_name": r.user_name,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


# ── Tool 8: Post Alert Comment ─────────────────────────────────────

@mcp.tool()
async def post_alert_comment(external_id: str, text: str) -> dict:
    """Saves an analysis or finding as a comment on an alert.

    Parameters:
    - external_id: the external ID of the alert (from list_alerts or search_feed)
    - text: the text to save (analysis, finding, recommendation)

    Use this tool after a detailed incident analysis so that others
    (and yourself in a later session) can access the findings.
    WRITE OPERATION — only execute after user confirmation."""
    import uuid as _uuid
    from app.models.workflow import AlertComment

    if not external_id or not text:
        return {"ok": False, "error": "external_id and text are required"}

    async with (await _get_db_session()) as db:
        db.add(AlertComment(
            id=_uuid.uuid4(),
            external_id=external_id,
            user_id=None,
            user_name="Computer (AI)",
            kind="ai",
            body=text[:2000],
        ))
        await db.commit()

    log.info("post_alert_comment: saved AI comment for %s", external_id)
    return {"ok": True, "external_id": external_id}


# ── Tool 9: Create Feed Exclusion ─────────────────────────────────

@mcp.tool()
async def create_feed_exclusion(
    name: str,
    query_string: str,
    source: str = "",
) -> dict:
    """Creates a feed exclusion: alerts matching the query are permanently
    hidden from the main feed (whitelist/suppress rule).

    Parameters:
    - name: short descriptive name for the exclusion (e.g. 'Backup jobs on backup01')
    - query_string: OpenSearch Lucene query to match alerts to suppress
                   Examples:
                   'title:*backup* AND metadata.host:backup01*'
                   'source:graylog AND title:*connection refused*'
                   'severity:low AND metadata.host:testserver*'
    - source: optional source restriction (checkmk/graylog/wazuh/icinga2/coroot).
              Leave empty to apply to all sources (cs-feed-*).

    Use this tool when the user says:
    - 'Permanently hide this alert'
    - 'Create an exclusion for ...'
    - 'Suppress these alerts'
    - 'Ignore alerts from host X'

    Note: The exclusion takes effect immediately."""
    from app.models.workflow import FeedSearch

    source = source.strip().lower()
    valid_sources = {"checkmk", "graylog", "wazuh", "icinga2", "coroot"}
    if source and source not in valid_sources:
        return {"error": f"Invalid source '{source}'. Allowed: {', '.join(sorted(valid_sources))}"}

    index_pattern = f"cs-feed-{source}" if source else "cs-feed-*"
    query_string = query_string.strip()
    if not query_string:
        return {"error": "query_string must not be empty"}

    async with (await _get_db_session()) as db:
        search = FeedSearch(
            user_id=None,
            index_pattern=index_pattern,
            name=name.strip() or "Hermes Exclusion",
            query_string=query_string,
            enabled=True,
            is_system=True,
            is_exclusion=True,
            position=97,
        )
        db.add(search)
        await db.commit()
        await db.refresh(search)

    log.info("create_feed_exclusion: '%s' query='%s' index='%s'", name, query_string, index_pattern)
    return {
        "ok": True,
        "id": str(search.id),
        "name": search.name,
        "query_string": query_string,
        "index_pattern": index_pattern,
        "message": f"Exclusion '{search.name}' created. Matching alerts will no longer appear in the feed.",
    }


@mcp.tool()
async def get_coroot_status(project: str = "") -> dict:
    """Returns a Coroot overview: active incidents and affected applications.

    Parameters:
    - project: optional project name filter (e.g. 'my-prod', 'my-stage').
               Leave empty for all configured projects.

    Useful when the user asks:
    - 'What does Coroot say?' / 'Any APM alerts?'
    - 'Which applications are having problems right now?'
    - 'Are there latency or availability issues?'"""
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.coroot import CorootConnector

    async with (await _get_db_session()) as db:
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "coroot",
                ConnectorConfig.enabled.is_(True),
            )
        )
        connectors = result.scalars().all()

    if not connectors:
        return {"error": "No active Coroot connector configured"}

    all_incidents: list[dict] = []
    errors: list[str] = []

    for cfg in connectors:
        try:
            creds = decrypt_credentials(cfg.encrypted_credentials)
            svc = CorootConnector(base_url=cfg.base_url, credentials=creds)
            incidents = await svc.get_incidents()
            if project:
                incidents = [i for i in incidents
                             if i["metadata"].get("project_name", "").lower() == project.lower()]
            all_incidents.extend(incidents)
        except Exception as exc:
            errors.append(f"{cfg.name}: {exc}")
            log.warning("get_coroot_status connector %s: %s", cfg.name, exc)

    by_project: dict[str, list] = {}
    for inc in all_incidents:
        p = inc["metadata"].get("project_name", "unknown")
        by_project.setdefault(p, []).append({
            "severity":    inc["severity"],
            "application": inc["metadata"].get("application", "?"),
            "description": inc["metadata"].get("short_description", "?"),
            "impact":      inc["metadata"].get("impact", 0),
            "since":       inc["metadata"].get("opened_at", ""),
            "external_id": inc["external_id"],
        })

    return {
        "total_incidents": len(all_incidents),
        "by_project": by_project,
        "errors": errors if errors else None,
    }


# ── Tool 11: IT-AIKB Knowledge Base ───────────────────────────────

@mcp.tool()
async def search_knowledge_base(query: str, deepsearch: bool = False) -> dict:
    """Searches the internal IT knowledge base (IT-AIKB / Confluence KB).

    Contains runbooks, server documentation, dependencies, configuration guides
    and KB articles for all systems.

    Parameters:
    - query: search term, e.g. 'HAProxy configuration', 'docker50 dependencies',
             'Graylog Filebeat setup', 'cue-integrations interfaces'
    - deepsearch: False (default) = fast OpenSearch hits with text excerpts
                  True = LLM-powered answer with source citations (slower, ~20-60s)

    Useful when:
    - You need documentation/configuration for a server
    - You want to look up dependencies between services
    - You need runbooks or guides for known problems
    - You are unsure about a technical solution and want to check internal docs
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.aikb import AIKBConnector

    async with (await _get_db_session()) as db:
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "aikb",
                ConnectorConfig.enabled.is_(True),
            )
        )
        conn = result.scalar_one_or_none()

    if not conn:
        return {"error": "No active IT-AIKB connector configured"}

    creds = decrypt_credentials(conn.encrypted_credentials)
    aikb = AIKBConnector(base_url=conn.base_url, credentials=creds)

    if deepsearch:
        res = await aikb.search_rag(query, deepsearch=True)
        answer = res.get("answer") or ""
        sources = res.get("results") or []
        return {
            "mode": "deepsearch",
            "query": query,
            "answer": answer,
            "sources": [
                {
                    "title": s.get("title", ""),
                    "space": s.get("space_key", ""),
                    "url": s.get("source_url", ""),
                    "excerpt": s.get("content", "")[:200],
                }
                for s in sources
            ],
        }
    else:
        hits = await aikb.search_opensearch(query, size=5)
        return {
            "mode": "opensearch",
            "query": query,
            "results": [
                {
                    "title": h.get("title", ""),
                    "space": h.get("space_key", ""),
                    "url": h.get("source_url", ""),
                    "excerpt": h.get("content", "")[:300],
                }
                for h in hits
            ],
            "count": len(hits),
        }


# ── DB session helper ──────────────────────────────────────────────

async def _get_db_session():
    """Returns an async context manager for a DB session."""
    from app.core.database import AsyncSessionLocal
    return AsyncSessionLocal()
