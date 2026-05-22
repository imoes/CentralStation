import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireNetworkTech
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.network import NetworkSwitchEvent

router = APIRouter(prefix="/network", tags=["network"])


@router.get("/switch-events", dependencies=[RequireNetworkTech])
async def list_switch_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    switch_type: str | None = Query(None),
    location_name: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    q = select(NetworkSwitchEvent).order_by(NetworkSwitchEvent.created_at.desc())
    if switch_type:
        q = q.where(NetworkSwitchEvent.switch_type == switch_type)
    if location_name:
        q = q.where(NetworkSwitchEvent.location_name == location_name)
    if severity:
        q = q.where(NetworkSwitchEvent.severity == severity)
    if status:
        q = q.where(NetworkSwitchEvent.status == status)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/switch-events/{event_id}/acknowledge", dependencies=[RequireNetworkTech])
async def acknowledge_switch_event(
    event_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(
        select(NetworkSwitchEvent).where(NetworkSwitchEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Event not found")

    event.status = "acknowledged"
    event.acknowledged_by = current_user.id
    db.add(AuditLog(action="switch_event_acknowledged", resource_type="network_event",
                    resource_id=str(event_id), user_id=current_user.id))
    await db.commit()
    return {"message": "Event acknowledged"}
