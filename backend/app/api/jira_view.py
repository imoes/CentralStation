"""Jira view — execute per-user JQL queries across all configured Jira connectors."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.connector import ConnectorConfig
from app.models.workflow import UserJiraQuery

router = APIRouter(prefix="/jira-view", tags=["jira-view"])

_JIRA_TYPES = ("jira", "jira_sd")


async def _get_all_jira_connectors(db: AsyncSession, user_id):
    """Return all enabled Jira connectors (both jira and jira_sd) accessible by this user."""
    result = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type.in_(_JIRA_TYPES),
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.type, ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
    )
    # Deduplicate: one connector per (type, base_url)
    seen: set[str] = set()
    connectors = []
    for c in result.scalars().all():
        key = f"{c.type}:{c.base_url}"
        if key not in seen:
            seen.add(key)
            connectors.append(c)
    return connectors


@router.get("/my-tickets")
async def my_tickets(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Execute all enabled per-user JQL queries against ALL Jira instances and merge results."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    from app.api.preferences import _ensure_default_jql_queries
    await _ensure_default_jql_queries(user.id, db)

    result = await db.execute(
        select(UserJiraQuery)
        .where(UserJiraQuery.user_id == user.id, UserJiraQuery.enabled.is_(True))
        .order_by(UserJiraQuery.position)
    )
    queries = result.scalars().all()
    if not queries:
        return []

    connectors = await _get_all_jira_connectors(db, user.id)
    if not connectors:
        return [
            {"id": str(q.id), "name": q.name, "jql": q.jql, "issues": [], "error": "Jira nicht konfiguriert"}
            for q in queries
        ]

    jira_clients = [
        (c, JiraConnector(base_url=c.base_url, credentials=decrypt_credentials(c.encrypted_credentials)))
        for c in connectors
    ]

    out = []
    for q in queries:
        merged: dict[str, dict] = {}  # key → issue, dedup across instances
        last_error: str | None = None
        had_success = False  # at least one connector responded without error
        for _conn, jira in jira_clients:
            try:
                issues = await jira.search_issues(
                    q.jql,
                    fields=["summary", "status", "priority", "assignee", "created", "updated", "issuetype", "comment"],
                )
                had_success = True
                for issue in issues:
                    key = issue.get("key", "")
                    if key and key not in merged:
                        merged[key] = issue
            except Exception as e:
                last_error = str(e)

        issues_list = sorted(merged.values(), key=lambda i: i.get("fields", {}).get("updated", ""), reverse=True)
        entry: dict = {"id": str(q.id), "name": q.name, "jql": q.jql, "issues": issues_list}
        if not had_success and last_error:
            entry["error"] = last_error
        out.append(entry)
    return out


@router.get("/issue/{issue_key}")
async def get_issue_detail(
    issue_key: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return full Jira issue detail: description + comment history.

    Tries all configured Jira connectors until one returns the issue.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    connectors = await _get_all_jira_connectors(db, user.id)
    if not connectors:
        raise HTTPException(status_code=503, detail="Jira nicht konfiguriert")

    last_err: Exception | None = None
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            return await jira.get_issue_detail(issue_key)
        except Exception as e:
            last_err = e

    raise HTTPException(status_code=404, detail=f"Ticket nicht gefunden: {last_err}")


@router.get("/hermes-context")
async def issue_hermes_context(
    issue_key: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Build a console starting prompt from a Jira/ServiceDesk ticket.

    Mirrors /feed/hermes-context for alerts: the backend assembles the context so the
    console receives one ready-to-send prompt instead of the frontend stitching text
    together. Covers both connector types (jira and jira_sd) via the shared lookup.

    The agent can actually work the ticket from there — the MCP server exposes
    jira_add_comment, jira_update_issue, jira_transition_issue and jira_get_transitions
    — so the prompt names those instead of leaving it to guess.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector, wiki_to_markdown

    connectors = await _get_all_jira_connectors(db, user.id)
    if not connectors:
        raise HTTPException(status_code=503, detail="Jira nicht konfiguriert")

    detail: dict | None = None
    last_err: Exception | None = None
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            detail = await jira.get_issue_detail(issue_key)
            break
        except Exception as e:  # try the next connector — the key may live elsewhere
            last_err = e
    if not detail:
        raise HTTPException(status_code=404, detail=f"Ticket nicht gefunden: {last_err}")

    key = detail.get("key") or issue_key
    summary = (detail.get("summary") or "").strip()
    # Jira Server/DC stores wiki markup ("h2. Aufgabe", "{{php.conf}}"). The console
    # renders the prompt as Markdown, so without this the ticket arrives as literal
    # markup instead of headings and inline code.
    description = wiki_to_markdown((detail.get("description") or "").strip(), heading_offset=1)

    lines = [
        f"Bearbeite das Ticket **{key}**: {summary or '(kein Titel)'}",
        "",
        f"- **Status:** {detail.get('status') or '?'}",
        f"- **Priorität:** {detail.get('priority') or '?'}",
        f"- **Zugewiesen an:** {detail.get('assignee') or '(niemand)'}",
        f"- **Aktualisiert:** {(detail.get('updated') or '')[:16]}",
        "",
        "## Beschreibung",
        description or "(keine Beschreibung hinterlegt)",
    ]

    comments = detail.get("comments") or []
    if comments:
        # Newest last so the conversation reads chronologically; cap the volume but say
        # so, rather than silently truncating the history.
        shown = comments[-15:]
        omitted = len(comments) - len(shown)
        lines += ["", f"## Verlauf ({len(comments)} Kommentare"
                      + (f", die {omitted} ältesten ausgelassen" if omitted else "") + ")"]
        for c in shown:
            body = wiki_to_markdown((c.get("body") or "").strip(), heading_offset=2)
            lines.append(f"\n**{c.get('author') or '?'}** ({(c.get('created') or '')[:16]}):\n{body}")

    lines += [
        "",
        "---",
        "Analysiere das Ticket und schlage konkrete nächste Schritte vor. Für Recherche "
        "im Bestand nutze die CentralStation-Werkzeuge (Feed, CheckMK, Wissensdatenbank). "
        "Am Ticket selbst kannst du mit `jira_add_comment`, `jira_update_issue` und "
        "`jira_transition_issue` arbeiten — frage vorher nach, bevor du etwas schreibst "
        "oder den Status änderst.",
    ]

    return {
        "prompt": "\n".join(lines),
        "label": key,
        "issue_key": key,
    }
