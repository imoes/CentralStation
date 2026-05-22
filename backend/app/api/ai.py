from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireSysAdmin
from app.core.database import get_db
from app.models.ai import AiAnalysis

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/analyses")
async def list_analyses(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
    agent_type: str | None = Query(None),
    limit: int = Query(20, le=100),
):
    q = select(AiAnalysis).order_by(AiAnalysis.run_at.desc())
    if agent_type:
        q = q.where(AiAnalysis.agent_type == agent_type)
    q = q.limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/trigger/{agent_type}", dependencies=[RequireSysAdmin])
async def trigger_agent(agent_type: str):
    if agent_type not in ("sysadmin", "network"):
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid agent type")

    from app.services.ai_agent.scheduler import run_sysadmin_agent, run_network_agent
    if agent_type == "sysadmin":
        import asyncio
        asyncio.create_task(run_sysadmin_agent())
    else:
        import asyncio
        asyncio.create_task(run_network_agent())
    return {"message": f"{agent_type} agent triggered"}


@router.get("/settings")
async def get_ai_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    from sqlalchemy import text
    # AI-Einstellungen liegen als Connector-Config vom Typ 'llm'
    from app.models.connector import ConnectorConfig
    result = await db.execute(
        select(ConnectorConfig).where(ConnectorConfig.type == "llm")
    )
    configs = result.scalars().all()
    return [{"id": str(c.id), "name": c.name, "type": c.type, "base_url": c.base_url}
            for c in configs]
