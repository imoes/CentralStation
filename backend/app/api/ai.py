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
    # When True, the created FeedSearch hides matching items from the main feed
    is_exclusion: bool = False
    name: str | None = None
    widget_type: str | None = None
    dashboard_id: uuid.UUID | None = None


def _fallback_search_assistant(message: str, lang: str = "en") -> dict:
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
        "reply": (
            "Ich habe daraus eine OpenSearch-Query vorbereitet."
            if lang == "de"
            else "I prepared an OpenSearch query from that."
        ),
        "index_pattern": index_pattern,
        "query_string": query_string,
        "actions": [],
    }


async def _llm_search_assistant(
    body: SearchAssistantRequest,
    db: AsyncSession,
    lang: str,
) -> dict:
    from app.services.ai_language import with_language
    from app.services.llm_client import generate_text
    from app.services.settings import get_llm_config

    llm = await get_llm_config(db)
    if not llm.is_configured:
        return _fallback_search_assistant(body.message, lang)

    system = with_language(
        "Du bist ein Konfigurations-Assistent fuer CentralStation. "
        "Erzeuge OpenSearch Lucene Query-Strings fuer die Indices cs-feed-checkmk, "
        "cs-feed-graylog, cs-feed-wazuh oder cs-feed-*. "
        "Antworte ausschliesslich als JSON mit: reply, index_pattern, query_string, suggested_name. "
        "suggested_name: kurzer, aussagekraeftiger Name fuer die Suche (max 60 Zeichen, kein 'KI-Suche'). "
        "Nutze keine Graylog-API-Syntax, sondern OpenSearch Query-String-Syntax.\n\n"
        "WICHTIGE FELDNAMEN (es gibt KEIN 'message:'-Feld!):\n"
        "  body:       - Vollstaendiger Alert-Text / Log-Nachricht (NICHT message:!)\n"
        "  title:      - Kurztitel des Alerts\n"
        "  severity:   - critical | high | medium | low | info\n"
        "  source:     - checkmk | wazuh | graylog | o365 | teams\n"
        "  host:       - Hostname\n"
        "  status:     - new | acknowledged | resolved\n"
        "               'nicht gelöst' / 'offen' / 'aktiv' = NOT status:resolved\n"
        "               NIEMALS status:new verwenden wenn der Nutzer 'nicht gelöst' meint!\n"
        "  metadata.hostgroups: - CheckMK Hostgruppe (z.B. cue-prod)\n"
        "  metadata.host:       - Hostname aus Metadaten\n"
        "  metadata.rule_id:    - Wazuh Rule-ID\n"
        "  metadata.rule_level: - Wazuh Rule-Level (>=7 fuer wichtige Alerts)\n"
        "  metadata.agent:      - Wazuh Agent-Name\n\n"
        "Beispiele:\n"
        "  body:\"/etc/patchmon/config.yml\" AND body:modified\n"
        "  severity:critical AND metadata.hostgroups:cue-prod\n"
        "  metadata.rule_level:>=7 AND NOT body:patchmon\n"
        "  (source:graylog OR source:checkmk) AND NOT status:resolved\n"
        "  source:wazuh AND severity:high AND NOT status:resolved\n\n"
        "WICHTIG: Wenn der Kontext eine bestehende Query enthaelt (z.B. 'Bestehende Query: ...'), "
        "dann ERWEITERE diese Query mit AND-Bedingungen. Ersetze sie NICHT komplett. "
        "Gib in query_string die vollstaendige erweiterte Query zurueck.\n",
        lang,
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
            "reply": str(data.get("reply") or ("Query vorbereitet." if lang == "de" else "Query prepared.")),
            "index_pattern": str(data.get("index_pattern") or "cs-feed-*"),
            "query_string": str(data.get("query_string") or ""),
            "suggested_name": str(data.get("suggested_name") or ""),
            "actions": [],
        }
    except Exception:
        return _fallback_search_assistant(body.message, lang)


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
    from app.services.ai_language import get_response_language_for_user

    lang = await get_response_language_for_user(db, current_user.id)
    result = await _llm_search_assistant(body, db, lang)
    actions: list[dict] = []

    if body.create_search:
        search_name = body.name or result.get("suggested_name") or ("Suche" if lang == "de" else "Search")
        search = FeedSearch(
            user_id=current_user.id,
            name=search_name,
            index_pattern=result["index_pattern"],
            query_string=result["query_string"],
            enabled=True,
            is_system=False,
            is_exclusion=body.is_exclusion,
        )
        db.add(search)
        await db.flush()
        actions.append({"type": "search_created", "id": str(search.id), "name": search_name})

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
        title = body.name or ("KI-Widget" if lang == "de" else "AI widget")
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


def _fallback_promql(message: str, lang: str = "en") -> dict:
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
        explanation = "CPU-Auslastung in Prozent (1 - idle rate)" if lang == "de" else "CPU usage in percent (1 - idle rate)"
    elif any(k in text for k in ("memory", "speicher", "ram")):
        promql = f'100 * (1 - node_memory_MemAvailable_bytes{label_str} / node_memory_MemTotal_bytes{label_str})'
        explanation = "RAM-Auslastung in Prozent" if lang == "de" else "RAM usage in percent"
    elif any(k in text for k in ("disk", "festplatte", "filesystem", "storage")):
        promql = f'100 * (1 - node_filesystem_free_bytes{label_str} / node_filesystem_size_bytes{label_str})'
        explanation = "Dateisystem-Auslastung in Prozent" if lang == "de" else "Filesystem usage in percent"
    elif any(k in text for k in ("network", "netzwerk", "traffic", "bytes")):
        promql = f'rate(node_network_receive_bytes_total{label_str}[5m])'
        explanation = "Netzwerk-Empfangsrate in Bytes/s" if lang == "de" else "Network receive rate in bytes/s"
    elif any(k in text for k in ("load", "last")):
        promql = f'node_load1{label_str}'
        explanation = "System-Load (1-Minuten-Durchschnitt)" if lang == "de" else "System load (1-minute average)"
    else:
        promql = f'up{label_str}'
        explanation = "Host-Verfügbarkeit (1 = erreichbar)" if lang == "de" else "Host availability (1 = reachable)"

    return {"promql": promql, "explanation": explanation}


@router.post("/promql-assistant")
async def promql_assistant(
    body: PromqlRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Convert natural language or Lucene-style search terms to PromQL."""
    from app.services.ai_language import get_response_language_for_user, with_language
    from app.services.llm_client import generate_text
    from app.services.settings import get_llm_config

    llm = await get_llm_config(db)
    lang = await get_response_language_for_user(db, current_user.id)
    if not llm.is_configured:
        return _fallback_promql(body.message, lang)

    system = with_language(
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
        "Antworte ausschliesslich als JSON: {\"promql\": \"<query>\", \"explanation\": \"<kurze Erklaerung>\"}",
        lang,
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
        return _fallback_promql(body.message, lang)


class DashboardAssistantRequest(BaseModel):
    message: str
    dashboard_id: uuid.UUID | None = None
    use_thinking: bool = False


@router.post("/dashboard-assistant")
async def dashboard_assistant(
    body: DashboardAssistantRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create or extend a dashboard from a natural language description.

    The LLM receives context (hostgroups, hosts with CheckMK alerts, feed query
    examples) and returns a JSON widget plan which is then executed directly.

    use_thinking=False (default): fast, sufficient for clear requests.
    use_thinking=True: extended reasoning for ambiguous/complex layouts.
    """
    from app.services.ai_language import get_response_language_for_user, with_language
    from app.services.llm_client import generate_text
    from app.services.settings import get_llm_config
    from app.services import feed_index

    llm = await get_llm_config(db)
    if not llm.is_configured:
        raise HTTPException(503, "LLM not configured")
    lang = await get_response_language_for_user(db, current_user.id)

    # ── Gather context from OpenSearch ──────────────────────────────────────
    # Available hostgroups
    filter_values = await feed_index.get_filter_values()
    hostgroups: list[str] = filter_values.get("hostgroups", [])

    # If a hostgroup is mentioned in the prompt, pre-fetch its CheckMK hosts
    mentioned_hg = next(
        (hg for hg in hostgroups if hg.lower() in body.message.lower()), None
    )
    hg_hosts: list[str] = []
    if mentioned_hg:
        items = await feed_index.search_by_query(
            index_pattern="cs-feed-checkmk",
            query_string=f"metadata.hostgroups:{mentioned_hg}",
            size=100,
            user_id=str(current_user.id),
        )
        hg_hosts = sorted({
            it.get("metadata", {}).get("host", "")
            for it in items
            if it.get("metadata", {}).get("host", "")
        })

    # ── Build LLM prompt ────────────────────────────────────────────────────
    context_lines = [
        f"Available hostgroups: {', '.join(hostgroups)}",
        f"GridStack: 12 columns total, cell-height=80px.",
        "Widget types: stat(2×2), list(4×3), donut(5×4), top_hosts(4×3), ai_summary(4×2), timeseries(5×4), grafana_panel(6×4)",
        "Timeseries config for CheckMK: {data_source:'checkmk', host, service, graph_index:0, hours:4}",
        "Timeseries config for Prometheus: {data_source:'prometheus', promql, step:'1m', hours:4, unit:'%'}",
        "Feed query for hostgroup filter: 'metadata.hostgroups:<hg>'",
        "Feed query for severity: 'severity:critical', 'severity:(critical OR high)'",
        "index_pattern options: cs-feed-checkmk, cs-feed-graylog, cs-feed-wazuh, cs-feed-*",
    ]
    if mentioned_hg and hg_hosts:
        context_lines.append(
            f"Hosts in hostgroup '{mentioned_hg}' (from CheckMK): {', '.join(hg_hosts)}"
        )

    system = with_language(
        "Du bist ein Dashboard-Konfigurations-Assistent fuer CentralStation.\n"
        "Erstelle ein vollstaendiges Dashboard-Layout basierend auf der Benutzeranfrage.\n\n"
        "Antworte AUSSCHLIESSLICH als JSON-Objekt (kein Markdown, kein Text davor/danach):\n"
        '{"dashboard_name":"...","dashboard_description":"...","widgets":[...]}\n\n'
        "Jedes Widget-Objekt hat DIESE Pflichtfelder:\n"
        "  widget_type (STRING, PFLICHT), title, gs_x, gs_y, gs_w, gs_h, config (dict)\n\n"
        "ERLAUBTE widget_type Werte und zugehoerige config-Formate:\n\n"
        '1. widget_type="stat"  (gs_w=2, gs_h=2) – Zaehler-Kachel\n'
        '   config: {"index_pattern":"cs-feed-checkmk","query_string":"severity:critical AND metadata.hostgroups:cue-prod"}\n\n'
        '2. widget_type="list"  (gs_w=4, gs_h=3) – Alert-Liste\n'
        '   config: {"index_pattern":"cs-feed-checkmk","query_string":"severity:(critical OR high) AND metadata.hostgroups:cue-prod","limit":10}\n\n'
        '3. widget_type="donut" (gs_w=5, gs_h=4) – Severity-Kreisdiagramm\n'
        '   config: {"index_pattern":"cs-feed-*","query_string":"metadata.hostgroups:cue-prod"}\n\n'
        '4. widget_type="top_hosts" (gs_w=4, gs_h=3) – Top problematische Hosts\n'
        '   config: {"index_pattern":"cs-feed-checkmk","query_string":"metadata.hostgroups:cue-prod AND NOT status:resolved","limit":5}\n\n'
        '5. widget_type="timeseries" (gs_w=12, gs_h=3) – Zeitreihe aus CheckMK:\n'
        '   config: {"data_source":"checkmk","host":"cue0111.ippen.media","service":"WSM PreviewBitmapCache Elaptime","graph_index":0,"hours":4}\n'
        '   ODER aus Prometheus:\n'
        '   config: {"data_source":"prometheus","promql":"rate(...)","step":"1m","hours":4,"unit":"%"}\n\n'
        "REGEL: Fuer Stat/List/Donut/Top_Hosts IMMER index_pattern+query_string verwenden (kein data_source!).\n"
        "REGEL: Fuer Timeseries IMMER data_source verwenden (kein index_pattern!).\n"
        "REGEL: widget_type MUSS exakt einer der 5 Strings sein: stat, list, donut, top_hosts, timeseries.\n\n"
        "Layout-Regeln:\n"
        "- gs_x + gs_w <= 12 (12 Spalten gesamt)\n"
        "- Zeile 0 (gs_y=0): Stat-Kacheln (gs_w=2, gs_h=2), groessere Widgets daneben\n"
        "- Mehrere Timeseries: vertikal stapeln (gs_y jeweils +3)\n\n"
        f"Kontext:\n" + "\n".join(context_lines),
        lang,
    )

    reasoning_effort = "low" if body.use_thinking else "none"
    raw = await generate_text(
        llm,
        [{"role": "system", "content": system}, {"role": "user", "content": body.message}],
        reasoning_effort=reasoning_effort,
        temperature=0.1,
        max_output_tokens=3000,
    )

    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]

    try:
        plan = json.loads(raw)
    except Exception:
        # Try to extract JSON from response
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            plan = json.loads(m.group(0))
        else:
            raise HTTPException(502, f"LLM returned invalid JSON: {raw[:300]}")

    # ── Create or use existing dashboard ────────────────────────────────────
    if body.dashboard_id:
        result = await db.execute(
            select(Dashboard).where(
                Dashboard.id == body.dashboard_id,
                Dashboard.user_id == current_user.id,
            )
        )
        dashboard = result.scalar_one_or_none()
        if not dashboard:
            raise HTTPException(404, "Dashboard not found")
    else:
        dashboard = Dashboard(
            id=uuid.uuid4(),
            user_id=current_user.id,
            name=plan.get("dashboard_name", "KI-Dashboard" if lang == "de" else "AI dashboard"),
            description=plan.get("dashboard_description", ""),
            is_default=False,
            position=99,
        )
        db.add(dashboard)
        await db.flush()

    # ── Create widgets ───────────────────────────────────────────────────────
    widgets_created = []
    for w in plan.get("widgets", []):
        widget = DashboardWidget(
            id=uuid.uuid4(),
            user_id=current_user.id,
            dashboard_id=dashboard.id,
            widget_type=w.get("widget_type", "list"),
            title=w.get("title", "Widget"),
            gs_x=int(w.get("gs_x", 0)),
            gs_y=int(w.get("gs_y", 0)),
            gs_w=int(w.get("gs_w", 4)),
            gs_h=int(w.get("gs_h", 3)),
            config=w.get("config", {}),
        )
        db.add(widget)
        widgets_created.append({
            "title": widget.title,
            "widget_type": widget.widget_type,
            "config": widget.config,
        })

    await db.commit()

    return {
        "dashboard_id": str(dashboard.id),
        "dashboard_name": dashboard.name,
        "widgets_created": widgets_created,
        "thinking_used": body.use_thinking,
        "reply": f"Dashboard '{dashboard.name}' mit {len(widgets_created)} Widgets erstellt.",
    }


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
    from app.services.feed_index import get_user_checkmk_host_scope
    user_host_scope  = await get_user_checkmk_host_scope(db, str(current_user.id))
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
                user_checkmk_host_scope=user_host_scope or None,
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
