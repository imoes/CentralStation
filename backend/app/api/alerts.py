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
    hours: int = Query(1, ge=1, le=168),
):
    """Count new alerts within the last `hours` hours (default: 1h = current situation)."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.status == "new", Alert.created_at >= since)
        .group_by(Alert.severity)
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("/cleanup", dependencies=[RequireSysAdmin])
async def cleanup_old_alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    older_than_days: int = Query(7, ge=1, le=365),
):
    """Mark alerts older than N days as resolved (default: 7 days)."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update as sql_update
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await db.execute(
        sql_update(Alert)
        .where(Alert.status == "new", Alert.created_at < cutoff)
        .values(status="resolved")
        .returning(Alert.id)
    )
    count = len(result.fetchall())
    db.add(AuditLog(
        action="alerts_cleanup",
        resource_type="alert",
        resource_id=f"older_than_{older_than_days}d",
        user_id=current_user.id,
        new_value={"resolved_count": count, "cutoff_days": older_than_days},
    ))
    await db.commit()
    return {"resolved": count, "cutoff_days": older_than_days}


@router.post("/aggregate", dependencies=[RequireSysAdmin])
async def trigger_aggregation(db: Annotated[AsyncSession, Depends(get_db)]):
    """Manually trigger alert collection from all enabled connectors."""
    from app.services.alert_aggregator import run_aggregation
    new_count = await run_aggregation(db)
    return {"new_alerts": new_count}


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
