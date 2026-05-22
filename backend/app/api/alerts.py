import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireSysAdmin
from app.core.database import get_db
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.schemas.alert import AlertFilter, AlertResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/", response_model=list[AlertResponse], dependencies=[RequireSysAdmin])
async def list_alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    q = select(Alert).order_by(Alert.created_at.desc())
    if source:
        q = q.where(Alert.source == source)
    if severity:
        q = q.where(Alert.severity == severity)
    if status:
        q = q.where(Alert.status == status)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/summary")
async def alert_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    from sqlalchemy import func
    result = await db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.status == "new")
        .group_by(Alert.severity)
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("/{alert_id}/acknowledge", dependencies=[RequireSysAdmin])
async def acknowledge_alert(
    alert_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Alert not found")

    alert.status = "acknowledged"
    alert.acknowledged_by = current_user.id
    db.add(AuditLog(action="alert_acknowledged", resource_type="alert",
                    resource_id=str(alert_id), user_id=current_user.id))
    await db.commit()
    return {"message": "Alert acknowledged"}
