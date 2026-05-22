"""Jira view — execute per-user JQL queries and return results."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.connector import ConnectorConfig
from app.models.workflow import UserJiraQuery

router = APIRouter(prefix="/jira-view", tags=["jira-view"])


@router.get("/my-tickets")
async def my_tickets(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Execute all enabled per-user JQL queries against Jira and return grouped results."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    result = await db.execute(
        select(UserJiraQuery)
        .where(UserJiraQuery.user_id == user.id, UserJiraQuery.enabled.is_(True))
        .order_by(UserJiraQuery.position)
    )
    queries = result.scalars().all()
    if not queries:
        return []

    conn_result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "jira",
            ConnectorConfig.enabled.is_(True),
        )
    )
    conn = conn_result.scalars().first()
    if not conn:
        return [
            {"id": str(q.id), "name": q.name, "jql": q.jql, "issues": [], "error": "Jira nicht konfiguriert"}
            for q in queries
        ]

    creds = decrypt_credentials(conn.encrypted_credentials)
    jira = JiraConnector(base_url=conn.base_url, credentials=creds)

    out = []
    for q in queries:
        try:
            issues = await jira.search_issues(
                q.jql,
                fields=["summary", "status", "priority", "assignee", "created", "updated", "issuetype"],
            )
            out.append({"id": str(q.id), "name": q.name, "jql": q.jql, "issues": issues})
        except Exception as e:
            out.append({"id": str(q.id), "name": q.name, "jql": q.jql, "issues": [], "error": str(e)})
    return out
