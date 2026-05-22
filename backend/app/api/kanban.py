import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAnyStaff
from app.core.database import get_db
from app.models.kanban import KanbanCard
from app.schemas.kanban import KanbanCardCreate, KanbanCardMove, KanbanCardResponse, KanbanCardUpdate

router = APIRouter(prefix="/kanban", tags=["kanban"])

VALID_STATUSES = {"backlog", "todo", "in_progress", "review", "done"}
JIRA_STATUS_CANDIDATES = {
    "backlog": ["Backlog", "Open", "Selected for Development"],
    "todo": ["To Do", "Open", "Ready"],
    "in_progress": ["In Progress", "Doing", "Implementing", "In Bearbeitung"],
    "review": ["Review", "In Review", "Testing", "QA"],
    "done": ["Done", "Resolved", "Closed", "Erledigt"],
}


def _map_jira_status(status_name: str | None) -> str:
    value = (status_name or "").lower()
    if value in {"open", "new", "backlog", "selected for development"}:
        return "backlog"
    if value in {"to do", "todo", "ready"}:
        return "todo"
    if value in {"in progress", "implementing", "bearbeitung", "doing"}:
        return "in_progress"
    if value in {"review", "in review", "qa", "testing"}:
        return "review"
    if value in {"done", "closed", "resolved", "erledigt"}:
        return "done"
    return "todo"


def _map_jira_priority(priority_name: str | None) -> str:
    value = (priority_name or "").lower()
    if value in {"highest", "critical", "blocker"}:
        return "critical"
    if value in {"high", "major"}:
        return "high"
    if value in {"low", "minor", "lowest"}:
        return "low"
    return "medium"


async def _get_preferred_connector(db: AsyncSession, connector_type: str, user_id):
    from app.models.connector import ConnectorConfig

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


async def _import_jira_cards(db: AsyncSession, current_user: CurrentUser) -> None:
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    connectors = []
    for connector_type in ("jira", "jira_sd"):
        connector = await _get_preferred_connector(db, connector_type, current_user.id)
        if connector:
            connectors.append(connector)

    seen_keys: set[str] = set()
    for connector in connectors:
        creds = decrypt_credentials(connector.encrypted_credentials)
        jira = JiraConnector(base_url=connector.base_url, credentials=creds)
        issues = await jira.search_issues(
            'assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC, updated DESC',
            fields=["summary", "status", "priority", "assignee", "description"],
        )
        for issue in issues:
            key = issue.get("key")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)

            fields = issue.get("fields") or {}
            status = _map_jira_status((fields.get("status") or {}).get("name"))
            priority = _map_jira_priority((fields.get("priority") or {}).get("name"))
            summary = fields.get("summary") or key
            description = fields.get("description")
            if isinstance(description, dict):
                description = description.get("content")
            if not isinstance(description, str):
                description = None

            result = await db.execute(select(KanbanCard).where(KanbanCard.jira_key == key))
            card = result.scalar_one_or_none()
            if card:
                card.title = summary
                card.description = description
                card.status = status
                card.priority = priority
                if not card.assigned_to:
                    card.assigned_to = current_user.id
            else:
                pos_result = await db.execute(
                    select(func.coalesce(func.max(KanbanCard.position), -1)).where(KanbanCard.status == status)
                )
                next_position = (pos_result.scalar_one() or -1) + 1
                db.add(KanbanCard(
                    title=summary,
                    description=description,
                    status=status,
                    priority=priority,
                    jira_key=key,
                    jira_issue_id=issue.get("id"),
                    assigned_to=current_user.id,
                    ai_generated=False,
                    position=next_position,
                ))

    await db.commit()


async def _get_jira_connector_for_user(db: AsyncSession, current_user: CurrentUser):
    connector = await _get_preferred_connector(db, "jira", current_user.id)
    if connector:
        return connector
    return await _get_preferred_connector(db, "jira_sd", current_user.id)


async def _sync_issue_fields(card: KanbanCard, current_user: CurrentUser, db: AsyncSession) -> None:
    if not card.jira_key:
        return
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    connector = await _get_jira_connector_for_user(db, current_user)
    if not connector:
        raise HTTPException(424, "Kein persönlicher Jira- oder ServiceDesk-Connector verfügbar")

    creds = decrypt_credentials(connector.encrypted_credentials)
    jira = JiraConnector(base_url=connector.base_url, credentials=creds)
    priority_map = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
    try:
        await jira.update_issue(
            card.jira_key,
            summary=card.title,
            description=card.description or card.title,
            priority=priority_map.get(card.priority, "Medium"),
        )
    except Exception as exc:
        raise HTTPException(424, f"Jira-Felder konnten nicht synchronisiert werden: {exc}") from exc


async def _sync_issue_status(card: KanbanCard, current_user: CurrentUser, db: AsyncSession) -> None:
    if not card.jira_key:
        return
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    connector = await _get_jira_connector_for_user(db, current_user)
    if not connector:
        raise HTTPException(424, "Kein persönlicher Jira- oder ServiceDesk-Connector verfügbar")

    creds = decrypt_credentials(connector.encrypted_credentials)
    jira = JiraConnector(base_url=connector.base_url, credentials=creds)
    try:
        matched = await jira.transition_issue_by_candidates(
            card.jira_key,
            JIRA_STATUS_CANDIDATES.get(card.status, []),
        )
    except Exception as exc:
        raise HTTPException(424, f"Jira-Status konnte nicht synchronisiert werden: {exc}") from exc
    if not matched:
        raise HTTPException(
            424,
            f"Keine passende Jira-Transition für Status '{card.status}' gefunden",
        )


@router.get("/", response_model=list[KanbanCardResponse], dependencies=[RequireAnyStaff])
async def list_cards(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    try:
        await _import_jira_cards(db, current_user)
    except Exception:
        pass

    result = await db.execute(
        select(KanbanCard).order_by(KanbanCard.status, KanbanCard.position)
    )
    return result.scalars().all()


@router.post("/", response_model=KanbanCardResponse, dependencies=[RequireAnyStaff])
async def create_card(
    data: KanbanCardCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    if data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")

    card = KanbanCard(**data.model_dump())
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


@router.patch("/{card_id}", response_model=KanbanCardResponse, dependencies=[RequireAnyStaff])
async def update_card(
    card_id: uuid.UUID,
    data: KanbanCardUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")

    if data.status and data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(card, field, value)

    try:
        await _sync_issue_fields(card, current_user, db)
    except Exception:
        await db.rollback()
        raise

    await db.commit()
    await db.refresh(card)

    # Push WebSocket update
    from app.api.ws import manager
    await manager.broadcast({
        "type": "kanban_update",
        "card_id": str(card_id),
        "status": card.status,
    })
    return card


@router.post("/{card_id}/move", response_model=KanbanCardResponse, dependencies=[RequireAnyStaff])
async def move_card(
    card_id: uuid.UUID,
    data: KanbanCardMove,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    if data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")

    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")

    card.status = data.status
    card.position = data.position

    try:
        await _sync_issue_status(card, current_user, db)
    except Exception:
        await db.rollback()
        raise

    await db.commit()
    await db.refresh(card)

    from app.api.ws import manager
    await manager.broadcast({
        "type": "kanban_move",
        "card_id": str(card_id),
        "status": data.status,
        "position": data.position,
    })
    return card


@router.delete("/{card_id}", dependencies=[RequireAnyStaff])
async def delete_card(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")
    await db.delete(card)
    await db.commit()
    return {"message": "Card deleted"}


@router.post("/{card_id}/jira-sync", dependencies=[RequireAnyStaff])
async def jira_sync(
    card_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    """Create a Jira ticket for this card (JQL dedup: reuses existing open ticket)."""
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")
    if card.jira_key:
        return {"jira_key": card.jira_key}

    connector = await _get_jira_connector_for_user(db, current_user)
    if not connector:
        raise HTTPException(424, "No enabled Jira connector configured")

    creds = decrypt_credentials(connector.encrypted_credentials)
    project = creds.get("project", "IMIT")
    jira = JiraConnector(base_url=connector.base_url, credentials=creds)

    existing_key = await jira.issue_exists_by_summary(project, card.title)
    if existing_key:
        card.jira_key = existing_key
        await db.commit()
        return {"jira_key": existing_key}

    priority_map = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
    issue = await jira.create_issue(
        project=project,
        summary=card.title,
        description=card.description or card.title,
        issue_type="Task",
        priority=priority_map.get(card.priority, "Medium"),
        labels=["CentralStation"],
    )
    card.jira_key = issue.get("key")
    await db.commit()
    return {"jira_key": card.jira_key}
