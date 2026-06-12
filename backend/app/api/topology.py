"""Topology API — infrastructure graph from NetBox + alert status."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireSysAdmin, get_db

router = APIRouter(prefix="/topology", tags=["topology"])


@router.get("/graph")
async def get_graph(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh: bool = Query(False),
):
    from app.services.topology_builder import build_topology
    return await build_topology(db, force_refresh=refresh)


@router.post("/extract-kb", dependencies=[RequireSysAdmin])
async def trigger_kb_extraction(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from app.core.tasks import run_background
    from app.services.topology_builder import run_topology_kb_extraction

    async def _job() -> None:
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await run_topology_kb_extraction(session)

    run_background(_job(), name="topology_kb_extraction")
    return {"message": "KB-Extraktion gestartet"}
