from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin
from app.core.database import get_db
from app.models.audit import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/", dependencies=[RequireAdmin])
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    q = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if action:
        q = q.where(AuditLog.action.like(f"%{action}%"))
    if resource_type:
        q = q.where(AuditLog.resource_type == resource_type)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "new_value": log.new_value,
            "ip_address": log.ip_address,
            "timestamp": log.timestamp.isoformat(),
        }
        for log in logs
    ]
