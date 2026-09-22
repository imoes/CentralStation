"""Jira view — execute per-user JQL queries across all configured Jira connectors."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
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
        merged: dict[str, dict] = {}  # connector + Jira id → issue
        last_error: str | None = None
        had_success = False  # at least one connector responded without error
        for conn, jira in jira_clients:
            try:
                issues = await jira.search_issues(
                    q.jql,
                    fields=["summary", "status", "priority", "assignee", "created", "updated", "issuetype", "comment"],
                )
                had_success = True
                for issue in issues:
                    key = issue.get("key", "")
                    issue_id = str(issue.get("id") or key)
                    identity = f"{conn.id}:{issue_id}"
                    if key and identity not in merged:
                        issue["_centralstation"] = {
                            "connector_id": str(conn.id),
                            "issue_id": issue_id,
                            "base_url": conn.base_url,
                        }
                        merged[identity] = issue
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
    connector_id: str | None = Query(None),
):
    """Return full Jira issue detail: description + comment history.

    Tries all configured Jira connectors until one returns the issue.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    connectors = await _get_all_jira_connectors(db, user.id)
    if connector_id:
        connectors = [c for c in connectors if str(c.id) == connector_id]
    if not connectors:
        raise HTTPException(status_code=503, detail="Jira nicht konfiguriert")

    last_err: Exception | None = None
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            detail = await jira.get_issue_detail(issue_key)
            detail["_centralstation"] = {
                "connector_id": str(conn.id),
                "issue_id": str(detail.get("id") or issue_key),
                "base_url": conn.base_url,
            }
            return detail
        except Exception as e:
            last_err = e

    raise HTTPException(status_code=404, detail=f"Ticket nicht gefunden: {last_err}")


@router.get("/hermes-context")
async def issue_hermes_context(
    issue_key: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    connector_id: str | None = Query(None),
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
    from app.services.connectors.jira import JiraConnector
    from app.services.ticket_activity import (
        TICKET_TASK_PROMPT, build_full_ticket_prompt, build_ticket_context,
        build_ticket_snapshot,
    )

    connectors = await _get_all_jira_connectors(db, user.id)
    if connector_id:
        connectors = [c for c in connectors if str(c.id) == connector_id]
    if not connectors:
        raise HTTPException(status_code=503, detail="Jira nicht konfiguriert")

    detail: dict | None = None
    last_err: Exception | None = None
    matched_connector = None
    for conn in connectors:
        try:
            creds = decrypt_credentials(conn.encrypted_credentials)
            jira = JiraConnector(base_url=conn.base_url, credentials=creds)
            detail = await jira.get_issue_detail(issue_key)
            matched_connector = conn
            break
        except Exception as e:  # try the next connector — the key may live elsewhere
            last_err = e
    if not detail:
        raise HTTPException(status_code=404, detail=f"Ticket nicht gefunden: {last_err}")

    key = detail.get("key") or issue_key
    prompt = build_full_ticket_prompt(detail, key)

    return {
        # prompt: Inhalt UND Aufgabe in einem Text. Bleibt, weil der Kontext-Hash
        # darauf beruht und ältere Aufrufer einen fertigen Prompt erwarten.
        "prompt": prompt,
        # Getrennte Teile für die Konsole: der Inhalt hängt als Kontext an der
        # Nachricht, die Aufgabe steht sichtbar im Eingabefeld.
        "ticket_context": build_ticket_context(detail, key),
        "task_prompt": TICKET_TASK_PROMPT,
        "label": key,
        "issue_key": key,
        "issue_id": str(detail.get("id") or key),
        "connector_id": str(matched_connector.id),
        "source_url": matched_connector.base_url,
        "snapshot": build_ticket_snapshot(detail),
        "context_hash": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
    }
