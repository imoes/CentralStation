"""User preferences, per-user JQL queries, setup wizard state."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.user import User
from app.models.workflow import UserJiraQuery, UserPreference

router = APIRouter(prefix="/preferences", tags=["preferences"])

DEFAULT_JQL_QUERIES = [
    {
        "name": "Meine offenen Tickets",
        "jql": "assignee = currentUser() AND status != Done ORDER BY updated DESC",
        "position": 0,
    },
    {
        "name": "Heute aktualisiert",
        "jql": "assignee = currentUser() AND updated >= startOfDay() ORDER BY updated DESC",
        "position": 1,
    },
    {
        "name": "Hohe Priorität",
        "jql": "assignee = currentUser() AND priority in (Highest, High) AND status != Done ORDER BY priority ASC, updated DESC",
        "position": 2,
    },
]


class PreferenceUpdate(BaseModel):
    setup_completed: bool | None = None
    jira_project: str | None = None
    jira_default_assignee_filter: str | None = None
    sla_notify_p1_minutes: int | None = None
    sla_notify_p2_minutes: int | None = None
    o365_mailbox: str | None = None
    o365_folder: str | None = None
    notification_settings: dict | None = None


class JQLQueryCreate(BaseModel):
    name: str
    jql: str
    position: int = 0
    show_in_widget: bool = True


class JQLQueryUpdate(BaseModel):
    name: str | None = None
    jql: str | None = None
    position: int | None = None
    enabled: bool | None = None
    show_in_widget: bool | None = None


class JQLGenerateRequest(BaseModel):
    description: str


async def _get_or_create_prefs(user: User, db: AsyncSession) -> UserPreference:
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    prefs = result.scalar_one_or_none()
    if not prefs:
        prefs = UserPreference(user_id=user.id)
        db.add(prefs)
        await db.flush()
        result2 = await db.execute(
            select(UserJiraQuery).where(UserJiraQuery.user_id == user.id)
        )
        if not result2.scalars().all():
            for q in DEFAULT_JQL_QUERIES:
                db.add(UserJiraQuery(user_id=user.id, **q))
        await db.commit()
        await db.refresh(prefs)
    return prefs


@router.get("")
async def get_preferences(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    prefs = await _get_or_create_prefs(user, db)
    return {
        "user_id": str(prefs.user_id),
        "setup_completed": prefs.setup_completed,
        "jira_project": prefs.jira_project,
        "jira_default_assignee_filter": prefs.jira_default_assignee_filter,
        "sla_notify_p1_minutes": prefs.sla_notify_p1_minutes,
        "sla_notify_p2_minutes": prefs.sla_notify_p2_minutes,
        "o365_mailbox": prefs.o365_mailbox,
        "o365_folder": prefs.o365_folder,
        "notification_settings": prefs.notification_settings,
    }


@router.patch("")
async def update_preferences(
    body: PreferenceUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    prefs = await _get_or_create_prefs(user, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(prefs, field, value)
    await db.commit()
    return {"ok": True}


# ── JQL Queries ────────────────────────────────────────────────────────────────

@router.get("/jira-queries")
async def list_jql_queries(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    await _get_or_create_prefs(user, db)
    result = await db.execute(
        select(UserJiraQuery)
        .where(UserJiraQuery.user_id == user.id)
        .order_by(UserJiraQuery.position)
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "jql": r.jql,
            "position": r.position,
            "enabled": r.enabled,
            "show_in_widget": r.show_in_widget,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/jira-queries", status_code=201)
async def create_jql_query(
    body: JQLQueryCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    q = UserJiraQuery(user_id=user.id, **body.model_dump())
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return {"id": str(q.id), "name": q.name, "jql": q.jql}


@router.post("/jira-queries/generate")
async def generate_jql_query(
    body: JQLGenerateRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Use LLM to generate a Jira JQL query from natural language."""
    from app.services.settings import get_llm_config
    from app.services.workflow_ai import generate_jql

    llm = await get_llm_config(db)
    if not llm.is_configured:
        raise HTTPException(503, "LLM not configured")
    return await generate_jql(llm, body.description)


@router.patch("/jira-queries/{query_id}")
async def update_jql_query(
    query_id: uuid.UUID,
    body: JQLQueryUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(UserJiraQuery).where(
            UserJiraQuery.id == query_id,
            UserJiraQuery.user_id == user.id,
        )
    )
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Query not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(q, field, value)
    await db.commit()
    return {"ok": True}


@router.delete("/jira-queries/{query_id}", status_code=204)
async def delete_jql_query(
    query_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(UserJiraQuery).where(
            UserJiraQuery.id == query_id,
            UserJiraQuery.user_id == user.id,
        )
    )
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(404, "Query not found")
    await db.delete(q)
    await db.commit()
