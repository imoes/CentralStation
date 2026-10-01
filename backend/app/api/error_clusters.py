"""API der Fehler-Cluster-Erkennung: Liste, Detail mit Verlauf, Muster stummschalten.

Liste und Detail sind auf den Host-Scope des Nutzers gefiltert (wie die Mails).
Stummschalten ist Admin-Sache und wird protokolliert: ein stummes Muster meldet nie.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireAdmin, get_db
from app.models.audit import AuditLog
from app.models.error_cluster import ErrorCluster, LogTemplate
from app.services.error_clusters import queries

router = APIRouter(prefix="/error-clusters", tags=["error-clusters"])


@router.get("")
async def list_clusters(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str = Query("open", pattern="^(open|closed|all)$"),
    hours: int = Query(24, ge=1, le=24 * 14),
    kind: str = Query("all", pattern="^(immediate|digest|all)$"),
    limit: int = Query(50, le=200),
):
    """Cluster der letzten `hours` Stunden, neueste zuerst, im Host-Scope des Nutzers."""
    return await queries.list_for_user(db, str(user.id), status=status, hours=hours,
                                       kind=kind, limit=limit)


@router.get("/{cluster_id}")
async def cluster_detail(cluster_id: uuid.UUID, user: CurrentUser,
                         db: Annotated[AsyncSession, Depends(get_db)]):
    """Ein Cluster mit Verlauf je 5 min (seit 1 h vor Beginn) und der Grundlast."""
    row = (await db.execute(select(ErrorCluster, LogTemplate)
                            .join(LogTemplate, LogTemplate.key == ErrorCluster.key, isouter=True)
                            .where(ErrorCluster.id == cluster_id))).first()
    if row is None:
        raise HTTPException(404, "Cluster nicht gefunden")
    ec, t = row
    if not await queries.in_scope(db, str(user.id), ec.sources or []):
        raise HTTPException(403, "Nicht in deinem Host-Bereich")
    end = ec.closed_at or datetime.now(timezone.utc)
    return {**queries.summary(ec, t),
            "series": await queries.series(db, ec.key, ec.started_at - timedelta(hours=1), end)}


class MuteBody(BaseModel):
    reason: str = ""


@router.post("/templates/{key}/mute", dependencies=[RequireAdmin])
async def mute_template(key: str, body: MuteBody, user: CurrentUser,
                        db: Annotated[AsyncSession, Depends(get_db)]):
    """Muster stummschalten: es meldet nie mehr (bis es freigegeben wird)."""
    t = (await db.execute(select(LogTemplate).where(LogTemplate.key == key))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Muster nicht gefunden")
    t.muted, t.muted_by, t.muted_reason = True, user.email, body.reason[:500] or None
    t.muted_at = datetime.now(timezone.utc)
    db.add(AuditLog(action="error_cluster_muted", resource_type="log_template", resource_id=key,
                    user_id=user.id, new_value={"reason": body.reason, "template": t.template[:300]}))
    await db.commit()
    _apply_mute(key, True)
    return {"key": key, "muted": True}


@router.delete("/templates/{key}/mute", dependencies=[RequireAdmin])
async def unmute_template(key: str, user: CurrentUser,
                          db: Annotated[AsyncSession, Depends(get_db)]):
    t = (await db.execute(select(LogTemplate).where(LogTemplate.key == key))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Muster nicht gefunden")
    t.muted, t.muted_by, t.muted_reason, t.muted_at = False, None, None, None
    db.add(AuditLog(action="error_cluster_unmuted", resource_type="log_template",
                    resource_id=key, user_id=user.id, new_value={}))
    await db.commit()
    _apply_mute(key, False)
    return {"key": key, "muted": False}


def _apply_mute(key: str, muted: bool) -> None:
    """Den laufenden Store sofort nachziehen — sonst wirkte es erst nach einem Neustart."""
    from app.services.error_clusters import runner
    if runner._rt is not None and key in runner._rt.store.templates:
        runner._rt.store.templates[key].muted = muted
