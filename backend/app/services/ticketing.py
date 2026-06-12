"""Shared ticket-creation logic for Jira and Jira Service Desk.

Centralises what used to live inline in app/api/feed.py so both the News Feed
and the Computer Console create tickets through the exact same path:

  - resolve_jira_connector()  → pick the user's jira / jira_sd connector
  - ai_ticket_draft()         → LLM formulates {summary, description, priority}
  - create_jira_issue()       → create the issue, return {ok, jira_key, url}

Both connector types use the same JiraConnector class — they only differ by
base_url / project / priority names of the target instance.
"""
from __future__ import annotations

import json as _json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Severity → Jira priority names (matches the configured instances).
SEVERITY_PRIORITY_MAP = {
    "critical": "Kritisch",
    "high": "Hoch",
    "medium": "Normal",
    "low": "Niedrig",
    "info": "Niedrig",
}
DEFAULT_PRIORITIES = ["Kritisch", "Hoch", "Normal", "Niedrig"]


async def resolve_jira_connector(db: AsyncSession, user_id, connector_type: str = "jira"):
    """Return the user's Jira connector of the given type (personal preferred, else global).

    connector_type: "jira" (regular Jira, e.g. IMSP) or "jira_sd" (ServiceDesk, e.g. IMIT).
    """
    from app.models.connector import ConnectorConfig

    r = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type == connector_type,
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def ai_ticket_draft(
    db: AsyncSession,
    user,
    context: str,
    severity_hint: str = "",
) -> dict:
    """LLM formulates a professional ticket from a free-form context string.

    `context` may be a monitoring-alert summary OR a Computer-Console transcript.
    Returns {summary, description, priority}. Falls back to a template when the
    LLM is unavailable or returns unparseable output.
    """
    severity = (severity_hint or "").strip().lower()
    priority = SEVERITY_PRIORITY_MAP.get(severity, "Normal")

    summary = ""
    description = ""
    try:
        from app.services.settings import get_active_llm_config
        from app.services.ai_language import with_language, get_response_language_for_user
        from app.services.llm_client import generate_text
        from app.services.dashboard.generative_designer import _strip_thinking

        llm_cfg = await get_active_llm_config(db)
        if llm_cfg.is_configured:
            lang = await get_response_language_for_user(db, user.id)
            system_prompt = with_language(
                "You are an IT operations engineer creating a Jira ticket. "
                "Produce a concise, professional ticket from the provided context "
                "(which may be a monitoring alert or a support-chat transcript). "
                "Return STRICT JSON only: "
                '{"summary": "<one line, max 120 chars>", "description": "<structured sections using '
                "Jira wiki markup (*bold*, h3. headings, ---- separators): "
                "h3. Problem | what failed, symptoms, affected service. "
                "h3. Affected System | host/service/component. "
                "h3. Root Cause | root cause if identified, else 'Under investigation'. "
                "h3. Evidence | REQUIRED — quote the specific log lines, metric values, timestamps, "
                "or alert text from the context that prove the problem and root cause. "
                "If AI analysis evidence is provided, include the most relevant entries verbatim. "
                "Never leave this section empty if any evidence exists in the context. "
                "h3. Proposed Solution | concrete fix or next steps proposed in the conversation — "
                "this section is REQUIRED and must reflect what the assistant actually recommended. "
                "h3. Steps Taken | what was already tried or investigated. "
                "No invented facts. Every claim in Root Cause and Proposed Solution must be backed by "
                "something in Evidence — no speculation without data.>\"}. "
                "The 'log source' field names the collector (Graylog/CheckMK), NOT the failing system.",
                lang,
            )
            raw = await generate_text(
                llm_cfg,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": context}],
            )
            clean = _strip_thinking(raw)
            lo, hi = clean.find("{"), clean.rfind("}")
            if lo >= 0 and hi > lo:
                try:
                    data = _json.loads(clean[lo:hi + 1])
                    summary = (data.get("summary") or "").strip()
                    description = (data.get("description") or "").strip()
                except Exception:
                    log.warning("ai_ticket_draft: AI returned unparseable JSON, using fallback")
    except Exception as e:
        log.warning("ai_ticket_draft: AI prefill failed, using template fallback: %s", e)

    if not summary:
        first_line = (context or "").strip().splitlines()[0] if context.strip() else "Incident"
        summary = first_line[:120]
    if not description:
        description = (context or "").strip()[:2000]

    return {"summary": summary, "description": description, "priority": priority}


async def create_jira_issue(
    db: AsyncSession,
    user,
    connector_type: str,
    project: str,
    summary: str,
    description: str,
    priority: str = "Normal",
    issue_type: str = "Serviceanfrage",
    labels: list[str] | None = None,
) -> dict:
    """Create a Jira/ServiceDesk issue in the chosen connector.

    Resolves the requested connector_type; falls back to the other Jira type if
    the requested one isn't configured. Builds the browse URL (create_issue does
    NOT return one). Returns {ok, jira_key, url}.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    conn = await resolve_jira_connector(db, user.id, connector_type)
    if not conn:
        other = "jira" if connector_type == "jira_sd" else "jira_sd"
        conn = await resolve_jira_connector(db, user.id, other)
    if not conn:
        return {"ok": False, "error": "No Jira connector configured"}

    creds = decrypt_credentials(conn.encrypted_credentials)
    jira = JiraConnector(base_url=conn.base_url, credentials=creds)

    async def _create(prio: str, send_labels: bool = True) -> dict:
        return await jira.create_issue(
            project=project,
            summary=summary[:200],
            description=description,
            issue_type=issue_type,
            priority=prio,
            labels=(labels or ["centralstation"]) if send_labels else None,
        )

    try:
        result = await _create(priority)
    except Exception as e:
        # Priority or labels may not be on the Jira screen → retry without both.
        log.warning("create_jira_issue failed (%s) — retrying without priority/labels", e)
        try:
            result = await _create("", send_labels=False)
        except Exception as e2:
            log.warning("create_jira_issue retry failed: %s", e2)
            return {"ok": False, "error": str(e2)[:200]}

    jira_key = result.get("key", "")
    base = (conn.base_url or "").rstrip("/")
    url = f"{base}/browse/{jira_key}" if jira_key and base else None
    return {"ok": True, "jira_key": jira_key, "url": url}
