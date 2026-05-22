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


async def _get_preferred_jira_connector(db: AsyncSession, user_id, connector_type: str = "jira"):
    result = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type == connector_type,
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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

    conn = await _get_preferred_jira_connector(db, user.id, "jira")
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
