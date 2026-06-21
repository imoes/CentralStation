"""Shared ticket-creation API — used by the Computer Console and the News Feed.

Lets any logged-in user create a Jira **or** Service-Desk ticket from a chosen
target, with an AI-formulated draft. Endpoints:

  GET  /tickets/targets   → available connectors (jira/jira_sd) + projects + priorities
  POST /tickets/draft     → AI formulates {summary, description, priority}
  POST /tickets/create    → create the issue → {ok, jira_key, url}
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.services.ticketing import (
    DEFAULT_PRIORITIES,
    ai_ticket_draft,
    create_jira_issue,
    resolve_jira_connector,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])
log = logging.getLogger(__name__)

_CONNECTOR_LABELS = {"jira": "Jira", "jira_sd": "Service Desk"}


# ── Targets ─────────────────────────────────────────────────────────

@router.get("/targets")
async def ticket_targets(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List available ticket targets (Jira / Service Desk) with their projects.

    Available to any logged-in user (unlike the admin-only settings variant).
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector
    from app.services.settings import get_setting

    default_project = (await get_setting(db, "jira.ticket_project")) or "IMIT"
    default_connector = (await get_setting(db, "jira.ticket_connector")) or "jira_sd"

    targets: list[dict] = []
    for ctype in ("jira_sd", "jira"):
        conn = await resolve_jira_connector(db, user.id, ctype)
        if not conn:
            continue
        projects: list[dict] = []
        issue_types: list[str] = []
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            projects = [{"key": p["key"], "name": p["name"]} for p in await jira.list_projects()]
            projects.sort(key=lambda p: p["key"])
            issue_types = await jira.list_issue_types()
        except Exception as e:
            log.warning("ticket_targets: %s connector list failed: %s", ctype, e)
        # Default issue type: prefer "Serviceanfrage" for SD, "Aufgabe" for Jira, else first.
        preferred = "Serviceanfrage" if ctype == "jira_sd" else "Aufgabe"
        default_issue_type = preferred if preferred in issue_types else (issue_types[0] if issue_types else "")
        targets.append({
            "connector_type": ctype,
            "label": _CONNECTOR_LABELS.get(ctype, ctype),
            "projects": projects,
            "default_project": default_project if any(p["key"] == default_project for p in projects) else (
                projects[0]["key"] if projects else default_project
            ),
            "issue_types": issue_types,
            "default_issue_type": default_issue_type,
        })

    if not targets:
        raise HTTPException(400, "Kein Jira-Connector konfiguriert")

    # Only offer default_connector if it actually resolved.
    if not any(t["connector_type"] == default_connector for t in targets):
        default_connector = targets[0]["connector_type"]

    return {
        "targets": targets,
        "default_connector": default_connector,
        "priorities": DEFAULT_PRIORITIES,
    }


# ── Draft ───────────────────────────────────────────────────────────

class DraftBody(BaseModel):
    feed_external_id: str | None = None
    transcript: str | None = None
    host: str | None = None
    severity: str | None = None


@router.post("/draft")
async def ticket_draft(
    body: DraftBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """AI pre-fills {summary, description, priority} from a feed item or a transcript."""
    severity = (body.severity or "").strip()
    context = ""

    if body.feed_external_id:
        from app.api.feed import _feed_item_by_external_id
        item = await _feed_item_by_external_id(body.feed_external_id)
        if not item:
            raise HTTPException(404, "Feed-Eintrag nicht gefunden")
        meta = item.get("metadata") or {}
        host = body.host or meta.get("host") or meta.get("agent") or meta.get("container_name") or ""
        application = meta.get("application") or ""
        severity = severity or item.get("severity", "info")
        service = meta.get("service", "")
        ctx = f"Source: {item.get('source', '')}\nSeverity: {severity}\nHost: {host}\n"
        if service:
            ctx += f"Service: {service}\n"
        if application:
            ctx += f"Application: {application}\n"
        ctx += f"Alert: {item.get('title', '')}\n"
        body_text = (item.get("body") or "")[:600]
        if body_text:
            ctx += f"Details: {body_text}\n"
        ai_insight = item.get("ai_insight") or ""
        if ai_insight:
            ctx += f"Prior AI insight: {ai_insight}\n"

        # Load AI analysis comments — these contain the evidence block (📎 Belege)
        # collected by the feed analysis pipeline and are the primary proof source.
        try:
            from app.models.workflow import AlertComment
            com_r = await db.execute(
                select(AlertComment)
                .where(
                    AlertComment.external_id == body.feed_external_id,
                    AlertComment.kind == "ai",
                )
                .order_by(AlertComment.created_at)
                .limit(3)
            )
            ai_comments = com_r.scalars().all()
            if ai_comments:
                ctx += "\n## KI-Analyse mit Belegen\n"
                for c in ai_comments:
                    ctx += c.body.strip() + "\n\n"
        except Exception as e:
            log.debug("ticket_draft: loading AI comments failed: %s", e)

        context = ctx
    elif body.transcript:
        host = (body.host or "").strip()
        prefix = f"Host/System: {host}\n" if host else ""
        # Keep the full transcript as-is so no log entries are silently dropped.
        # Only truncate at a high limit; when forced, extract code/log blocks first
        # so the AI always receives the verbatim evidence even in long sessions.
        tr = body.transcript
        MAX_TR = 24000
        if len(tr) > MAX_TR:
            import re as _re
            # Pull out every code/log block from the full transcript before truncating.
            log_blocks = _re.findall(r'```[\s\S]*?```|`[^`\n]{40,}`', tr)
            tr = tr[:6000] + "\n\n[... Gesprächsmitte gekürzt ...]\n\n" + tr[-14000:]
            if log_blocks:
                unique_blocks = list(dict.fromkeys(log_blocks))[:30]
                tr += "\n\n## Vollständige Log-Blöcke (aus dem Gespräch extrahiert)\n"
                tr += "\n\n".join(unique_blocks)
        context = (
            f"{prefix}Support-chat transcript between an operator and the AI assistant. "
            f"Create a ticket capturing the problem, investigation and proposed solution:\n\n{tr}"
        )
    else:
        raise HTTPException(400, "feed_external_id oder transcript erforderlich")

    return await ai_ticket_draft(db, user, context, severity_hint=severity)


# ── Create ──────────────────────────────────────────────────────────

class CreateBody(BaseModel):
    connector_type: str = "jira_sd"
    project: str
    summary: str
    description: str = ""
    priority: str = "Normal"
    issue_type: str = "Serviceanfrage"
    labels: list[str] | None = None
    feed_external_id: str | None = None


@router.post("/create")
async def ticket_create(
    body: CreateBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create the ticket in the chosen connector; returns {ok, jira_key, url}."""
    summary = (body.summary or "").strip()
    project = (body.project or "").strip()
    if not summary or not project:
        raise HTTPException(400, "Summary und Projekt sind erforderlich")

    result = await create_jira_issue(
        db, user,
        connector_type=body.connector_type,
        project=project,
        summary=summary,
        description=(body.description or "").strip(),
        priority=(body.priority or "Normal").strip(),
        issue_type=(body.issue_type or "Serviceanfrage").strip(),
        labels=body.labels,
    )
    if not result.get("ok"):
        raise HTTPException(502, f"Ticket konnte nicht erstellt werden: {result.get('error', '')}")

    jira_key = result.get("jira_key", "")

    # When created from a feed item, record it in the collaboration timeline.
    if body.feed_external_id and jira_key:
        try:
            from app.api.feed import _get_or_create_collab, _add_timeline, _broadcast_collab
            collab = await _get_or_create_collab(body.feed_external_id, db)
            await _add_timeline(
                body.feed_external_id, user.id, user.full_name or user.email, "comment",
                f"🎫 Jira-Ticket erstellt: {jira_key}", db,
            )
            await db.commit()
            await _broadcast_collab(
                body.feed_external_id, "comment", user.full_name or user.email,
                collab.work_status, f"Jira-Ticket erstellt: {jira_key}",
            )
        except Exception as e:
            log.debug("ticket_create: timeline record failed: %s", e)

    return result
