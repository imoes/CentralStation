"""Remediation API — approve, reject and monitor AWX job executions."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.remediation import RemediationProposal

log = logging.getLogger(__name__)
router = APIRouter(prefix="/remediations", tags=["remediations"])

TERMINAL_STATUSES = {"successful", "failed", "error", "canceled"}


def _require_sysadmin(user) -> None:
    if user.role not in ("admin", "sysadmin"):
        raise HTTPException(403, "Requires admin or sysadmin role")


def _to_dict(r: RemediationProposal) -> dict:
    return {
        "id": str(r.id),
        "created_at": r.created_at.isoformat(),
        "external_id": r.external_id,
        "host": r.host,
        "finding_title": r.finding_title,
        "rationale": r.rationale,
        "awx_template_id": r.awx_template_id,
        "awx_template_name": r.awx_template_name,
        "extra_vars": r.extra_vars or {},
        "risk": r.risk,
        "status": r.status,
        "awx_job_id": r.awx_job_id,
        "stdout": r.stdout,
        "approved_by": str(r.approved_by) if r.approved_by else None,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "analysis_id": str(r.analysis_id) if r.analysis_id else None,
    }


@router.get("")
async def list_remediations(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None),
):
    _require_sysadmin(user)
    q = select(RemediationProposal).order_by(RemediationProposal.created_at.desc())
    if status:
        q = q.where(RemediationProposal.status == status)
    result = await db.execute(q.limit(200))
    return [_to_dict(r) for r in result.scalars().all()]


@router.get("/{rid}")
async def get_remediation(
    rid: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_sysadmin(user)
    r = (await db.execute(select(RemediationProposal).where(RemediationProposal.id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Not found")
    return _to_dict(r)


class _RejectBody(BaseModel):
    reason: str | None = None


@router.post("/{rid}/reject")
async def reject_remediation(
    rid: uuid.UUID,
    body: _RejectBody,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_sysadmin(user)
    r = (await db.execute(select(RemediationProposal).where(RemediationProposal.id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Not found")
    if r.status not in ("proposed",):
        raise HTTPException(400, f"Cannot reject from status '{r.status}'")
    r.status = "rejected"
    await db.commit()
    return {"ok": True}


@router.post("/{rid}/approve")
async def approve_remediation(
    rid: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_sysadmin(user)
    r = (await db.execute(select(RemediationProposal).where(RemediationProposal.id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Not found")
    if r.status not in ("proposed",):
        raise HTTPException(400, f"Cannot approve from status '{r.status}'")
    if not r.awx_template_id:
        raise HTTPException(400, "No AWX template assigned")

    # Load AWX connector
    from app.models.connector import ConnectorConfig
    from app.core.security import decrypt_credentials
    from app.services.connectors.awx import AWXConnector

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "awx",
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(503, "AWX connector not configured")

    creds = decrypt_credentials(cfg.encrypted_credentials)
    awx = AWXConnector(base_url=cfg.base_url, credentials=creds)

    # Launch job
    try:
        launched = await awx.launch(r.awx_template_id, extra_vars=r.extra_vars or {})
    except Exception as exc:
        raise HTTPException(502, f"AWX launch failed: {exc}") from exc

    r.status = "running"
    r.awx_job_id = launched.get("job")
    r.approved_by = user.id
    r.approved_at = datetime.now(timezone.utc)
    await db.commit()

    # Poll job in background
    asyncio.ensure_future(_poll_job(str(r.id), launched.get("job"), awx))

    return {"ok": True, "awx_job_id": r.awx_job_id}


async def _poll_job(proposal_id: str, job_id: int | None, awx) -> None:
    """Poll the AWX job every 10s until terminal, then update the DB."""
    if not job_id:
        return

    from app.core.database import AsyncSessionLocal
    import asyncio

    for _ in range(60):  # max 10 minutes
        await asyncio.sleep(10)
        try:
            async with AsyncSessionLocal() as db:
                job = await awx.get_job(job_id)
                status = job.get("status", "")
                stdout = None
                if status in TERMINAL_STATUSES:
                    try:
                        stdout = await awx.get_job_stdout(job_id)
                    except Exception:
                        pass

                r = (await db.execute(
                    select(RemediationProposal).where(RemediationProposal.id == uuid.UUID(proposal_id))
                )).scalar_one_or_none()
                if not r:
                    return

                r.status = "succeeded" if status == "successful" else status
                if stdout:
                    r.stdout = stdout[:10000]
                await db.commit()

                # WS broadcast
                try:
                    from app.api.ws import manager
                    await manager.broadcast(
                        {"type": "remediation_update", "id": proposal_id, "status": r.status},
                        roles=["admin", "sysadmin"],
                    )
                except Exception:
                    pass

                if status in TERMINAL_STATUSES:
                    log.info("remediation %s: job %s finished → %s", proposal_id[:8], job_id, r.status)
                    return
        except Exception as exc:
            log.warning("remediation poll %s: %s", proposal_id[:8], exc)
