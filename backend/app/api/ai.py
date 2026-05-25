import uuid
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, RequireSysAdmin
from app.core.database import get_db
from app.models.ai import AiAnalysis
from app.models.workflow import Dashboard, DashboardWidget, FeedSearch

router = APIRouter(prefix="/ai", tags=["ai"])


class SearchAssistantRequest(BaseModel):
    message: str
    context: str | None = None
    create_search: bool = False
    create_widget: bool = False
    name: str | None = None
    widget_type: str | None = None
    dashboard_id: uuid.UUID | None = None


def _fallback_search_assistant(message: str) -> dict:
    text = message.lower()
    index_pattern = "cs-feed-*"
    if "wazuh" in text:
        index_pattern = "cs-feed-wazuh"
    elif "graylog" in text or "log" in text:
        index_pattern = "cs-feed-graylog"
    elif "checkmk" in text or "monitoring" in text:
        index_pattern = "cs-feed-checkmk"

    query_parts: list[str] = []
    if "kritisch" in text or "critical" in text:
        query_parts.append("severity:critical")
    elif "hoch" in text or "high" in text:
        query_parts.append("severity:high")
    if "fehler" in text or "error" in text:
        query_parts.append("(body:error OR title:error OR metadata.level:<=4)")

    import re
    hosts = re.findall(r"\b(?:docker|srv|web|db|nsa|nss|nsc)[a-z0-9-]*\b", text)
    if hosts:
        host_query = " OR ".join(f"metadata.host:{host}*" for host in hosts[:5])
        query_parts.append(f"({host_query})")

    query_string = " AND ".join(query_parts)
    return {
        "reply": "Ich habe daraus eine OpenSearch-Query vorbereitet.",
        "index_pattern": index_pattern,
        "query_string": query_string,
        "actions": [],
    }


async def _llm_search_assistant(body: SearchAssistantRequest, db: AsyncSession) -> dict:
    from app.services.llm_client import generate_text
    from app.services.settings import get_llm_config

    llm = await get_llm_config(db)
    if not llm.is_configured:
        return _fallback_search_assistant(body.message)

    system = (
        "Du bist ein Konfigurations-Assistent fuer CentralStation. "
        "Erzeuge OpenSearch Lucene Query-Strings fuer die Indices cs-feed-checkmk, "
        "cs-feed-graylog, cs-feed-wazuh oder cs-feed-*. "
        "Antworte ausschliesslich als JSON mit: reply, index_pattern, query_string. "
        "Nutze keine Graylog-API-Syntax, sondern OpenSearch Query-String-Syntax."
    )
    user = f"Kontext: {body.context or '-'}\nAnfrage: {body.message}"
    raw = await generate_text(
        llm,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        reasoning_effort="none",
        temperature=0.1,
        max_output_tokens=500,
    )
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM returned non-object JSON")
        return {
            "reply": str(data.get("reply") or "Query vorbereitet."),
            "index_pattern": str(data.get("index_pattern") or "cs-feed-*"),
            "query_string": str(data.get("query_string") or ""),
            "actions": [],
        }
    except Exception:
        return _fallback_search_assistant(body.message)


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


@router.post("/search-assistant")
async def search_assistant(
    body: SearchAssistantRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Generate OpenSearch queries and optionally persist searches/widgets."""
    result = await _llm_search_assistant(body, db)
    actions: list[dict] = []

    if body.create_search:
        search = FeedSearch(
            user_id=current_user.id,
            name=body.name or "KI-Suche",
            index_pattern=result["index_pattern"],
            query_string=result["query_string"],
            enabled=True,
            is_system=False,
        )
        db.add(search)
        await db.flush()
        actions.append({"type": "search_created", "id": str(search.id)})

    if body.create_widget:
        if body.dashboard_id:
            dashboard_result = await db.execute(
                select(Dashboard).where(
                    Dashboard.id == body.dashboard_id,
                    Dashboard.user_id == current_user.id,
                )
            )
            if not dashboard_result.scalar_one_or_none():
                raise HTTPException(404, "Dashboard not found")
        widget_type = body.widget_type or "list"
        title = body.name or "KI-Widget"
        config = {
            "index_pattern": result["index_pattern"],
            "query_string": result["query_string"],
            "limit": 8,
        }
        widget = DashboardWidget(
            user_id=current_user.id,
            dashboard_id=body.dashboard_id,
            widget_type=widget_type,
            title=title,
            gs_x=0,
            gs_y=0,
            gs_w=4 if widget_type != "stat" else 2,
            gs_h=3 if widget_type != "stat" else 2,
            config=config,
        )
        db.add(widget)
        await db.flush()
        actions.append({"type": "widget_created", "id": str(widget.id)})

    await db.commit()
    result["actions"] = actions
    return result


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


class PromqlRequest(BaseModel):
    message: str


def _fallback_promql(message: str) -> dict:
    """Heuristic Lucene-style → PromQL conversion without LLM."""
    text = message.lower()
    labels: list[str] = []

    import re
    # Extract host/instance hints (hostname:docker086 or just docker086)
    host_match = re.search(r"(?:host(?:name)?|instance)[:\s=]+([a-z0-9._-]+)", text)
    if not host_match:
        host_match = re.search(r"\b(docker[0-9]+|srv[0-9]+|web[0-9]+|db[0-9]+)\b", text)
    if host_match:
        labels.append(f'instance="{host_match.group(1)}:9100"')

    label_str = "{" + ", ".join(labels) + "}" if labels else ""

    if any(k in text for k in ("cpu", "prozessor", "auslastung")):
        if labels and host_match:
            promql = f'100 - (avg(rate(node_cpu_seconds_total{{instance="{host_match.group(1)}:9100",mode="idle"}}[5m])) * 100)'
        else:
            promql = '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        explanation = "CPU-Auslastung in Prozent (1 - idle rate)"
    elif any(k in text for k in ("memory", "speicher", "ram")):
        promql = f'100 * (1 - node_memory_MemAvailable_bytes{label_str} / node_memory_MemTotal_bytes{label_str})'
        explanation = "RAM-Auslastung in Prozent"
    elif any(k in text for k in ("disk", "festplatte", "filesystem", "storage")):
        promql = f'100 * (1 - node_filesystem_free_bytes{label_str} / node_filesystem_size_bytes{label_str})'
        explanation = "Dateisystem-Auslastung in Prozent"
    elif any(k in text for k in ("network", "netzwerk", "traffic", "bytes")):
        promql = f'rate(node_network_receive_bytes_total{label_str}[5m])'
        explanation = "Netzwerk-Empfangsrate in Bytes/s"
    elif any(k in text for k in ("load", "last")):
        promql = f'node_load1{label_str}'
        explanation = "System-Load (1-Minuten-Durchschnitt)"
    else:
        promql = f'up{label_str}'
        explanation = "Host-Verfügbarkeit (1 = erreichbar)"

    return {"promql": promql, "explanation": explanation}


@router.post("/promql-assistant")
async def promql_assistant(
    body: PromqlRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Convert natural language or Lucene-style search terms to PromQL."""
    from app.services.llm_client import generate_text
    from app.services.settings import get_llm_config

    llm = await get_llm_config(db)
    if not llm.is_configured:
        return _fallback_promql(body.message)

    system = (
        "Du bist ein Prometheus-Experte. Konvertiere natuerlichsprachliche Beschreibungen "
        "oder Lucene-aehnliche Suchterme in valide PromQL-Queries.\n\n"
        "Verfuegbare node_exporter Metriken (Auswahl):\n"
        "- CPU: node_cpu_seconds_total{mode='idle'|'user'|'system'}\n"
        "- RAM: node_memory_MemTotal_bytes, node_memory_MemAvailable_bytes\n"
        "- Disk I/O: node_disk_io_time_seconds_total, node_disk_read_bytes_total\n"
        "- Netzwerk: node_network_receive_bytes_total, node_network_transmit_bytes_total\n"
        "- Dateisystem: node_filesystem_size_bytes, node_filesystem_free_bytes\n"
        "- Load: node_load1, node_load5, node_load15\n"
        "- Uptime: node_boot_time_seconds\n"
        "- CheckMK: cmk_service_state{hostname='..'}, cmk_host_state{hostname='..'}\n\n"
        "Lucene-Syntax-Mapping:\n"
        "- host:docker086 oder hostname:docker086 -> {instance='docker086:9100'}\n"
        "- metric:cpu -> node_cpu_seconds_total\n"
        "- NOT mode:idle -> {mode!='idle'}\n\n"
        "Antworte ausschliesslich als JSON: {\"promql\": \"<query>\", \"explanation\": \"<kurze Erklaerung>\"}"
    )

    raw = await generate_text(
        llm,
        [{"role": "system", "content": system}, {"role": "user", "content": body.message}],
        reasoning_effort="none",
        temperature=0.1,
        max_output_tokens=300,
    )
    try:
        data = json.loads(raw)
        return {
            "promql": str(data.get("promql") or ""),
            "explanation": str(data.get("explanation") or ""),
        }
    except Exception:
        return _fallback_promql(body.message)


@router.post("/trigger/{agent_type}", dependencies=[RequireSysAdmin])
async def trigger_agent(
    agent_type: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if agent_type not in ("sysadmin", "network"):
        raise HTTPException(400, "Invalid agent type. Use: sysadmin, network")

    import asyncio
    from app.core.database import AsyncSessionLocal
    from app.models.workflow import UserPreference
    from sqlalchemy import select as sa_select

    # Load the triggering user's personal CheckMK filter preferences
    result = await db.execute(
        sa_select(UserPreference).where(UserPreference.user_id == current_user.id)
    )
    prefs = result.scalar_one_or_none()
    user_locations   = (prefs.checkmk_locations   or []) if prefs else []
    user_ve          = (prefs.checkmk_ve          or []) if prefs else []
    user_criticality = (prefs.checkmk_criticality or []) if prefs else []
    user_os          = (prefs.checkmk_os          or []) if prefs else []
    # Minimum alert age: only analyse problems that have persisted this long
    min_age_minutes  = (prefs.feed_checkmk_min_age_minutes or 10) if prefs else 10

    async def _run_sysadmin():
        from app.services.ai_agent.graph import run_sysadmin_workflow
        async with AsyncSessionLocal() as new_db:
            await run_sysadmin_workflow(
                new_db,
                user_checkmk_locations=user_locations or None,
                user_checkmk_ve=user_ve or None,
                user_checkmk_criticality=user_criticality or None,
                user_checkmk_os=user_os or None,
                min_age_minutes=min_age_minutes,
            )

    async def _run_network():
        from app.services.ai_agent.network_graph import run_network_workflow
        async with AsyncSessionLocal() as new_db:
            await run_network_workflow(new_db)

    if agent_type == "sysadmin":
        asyncio.create_task(_run_sysadmin())
    else:
        asyncio.create_task(_run_network())
    return {"message": f"{agent_type} agent triggered"}
