import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
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
    analyses = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "agent_type": a.agent_type,
            "run_at": a.run_at.isoformat(),
            "severity_summary": a.severity_summary,
            "findings_count": len(a.findings or []),
            "recommendations_count": len(a.recommendations or []),
            "jira_tickets_created": a.jira_tickets_created or [],
            "findings": a.findings or [],
            "recommendations": a.recommendations or [],
            "rag_queries_used": a.rag_queries_used or [],
            "token_usage": a.token_usage or {},
        }
        for a in analyses
    ]


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
):
    result = await db.execute(select(AiAnalysis).where(AiAnalysis.id == analysis_id))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Analysis not found")
    return {
        "id": str(a.id),
        "agent_type": a.agent_type,
        "run_at": a.run_at.isoformat(),
        "severity_summary": a.severity_summary,
        "findings": a.findings or [],
        "recommendations": a.recommendations or [],
        "rag_queries_used": a.rag_queries_used or [],
        "jira_tickets_created": a.jira_tickets_created or [],
        "token_usage": a.token_usage or {},
    }


@router.post("/trigger/{agent_type}", dependencies=[RequireSysAdmin])
async def trigger_agent(agent_type: str, db: Annotated[AsyncSession, Depends(get_db)]):
    if agent_type not in ("sysadmin", "network"):
        raise HTTPException(400, "Invalid agent type. Use: sysadmin, network")

    import asyncio
    if agent_type == "sysadmin":
        from app.services.ai_agent.graph import run_sysadmin_workflow
        asyncio.create_task(run_sysadmin_workflow(db))
    else:
        from app.services.ai_agent.network_graph import run_network_workflow
        asyncio.create_task(run_network_workflow(db))
    return {"message": f"{agent_type} agent triggered"}
