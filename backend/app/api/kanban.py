import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAnyStaff
from app.core.database import get_db
from app.models.kanban import KanbanCard
from app.schemas.kanban import KanbanCardCreate, KanbanCardMove, KanbanCardResponse, KanbanCardUpdate

router = APIRouter(prefix="/kanban", tags=["kanban"])

VALID_STATUSES = {"backlog", "todo", "in_progress", "review", "done"}


@router.get("/", response_model=list[KanbanCardResponse], dependencies=[RequireAnyStaff])
async def list_cards(db: Annotated[AsyncSession, Depends(get_db)]):
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
):
    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")

    if data.status and data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(card, field, value)

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
):
    if data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {VALID_STATUSES}")

    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")

    card.status = data.status
    card.position = data.position
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
):
    """Create a Jira ticket for this card (JQL dedup: reuses existing open ticket)."""
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    result = await db.execute(select(KanbanCard).where(KanbanCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found")
    if card.jira_key:
        return {"jira_key": card.jira_key}

    conn_result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type.in_(["jira", "jira_sd"]),
            ConnectorConfig.enabled.is_(True),
        )
    )
    connector = conn_result.scalars().first()
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
