"""SysAdmin AI Agent — LangGraph 5-node workflow.

Nodes:
  collect_data   → fetch alerts from all enabled connectors
  enrich         → add location/host info (ID-Generator + NetBox)
  rag_lookup     → LLM decides: standard /search vs DeepSearch SSE
  analyze        → Qwen 35B/79B structured output analysis
  act            → create Jira tickets, persist to DB, push via WS
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from langgraph.graph import END, StateGraph

from app.services.llm_client import generate_text
from app.services.ai_agent.models import AgentState, AnalysisResult, Finding, Recommendation
from app.services.ai_agent.prompts import (
    RAG_DECISION_PROMPT, SEARXNG_HYDE_PROMPT, SYSADMIN_SYSTEM,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────
# Node 1: collect_data
# ─────────────────────────────────────────────────
async def collect_data(state: dict, db: Any) -> dict:
    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type.in_(["checkmk", "graylog", "wazuh"]),
            ConnectorConfig.enabled.is_(True),
        )
    )
    connectors = result.scalars().all()

    raw_alerts: list[dict] = []
    for connector in connectors:
        creds = decrypt_credentials(connector.encrypted_credentials)
        try:
            if connector.type == "checkmk":
                from app.services.connectors.checkmk import CheckMKConnector
                svc = CheckMKConnector(base_url=connector.base_url, credentials=creds)
                items = await svc.get_problems(time_range_minutes=60)
                raw_alerts.extend(items)
            elif connector.type == "graylog":
                from app.services.connectors.graylog import GraylogConnector
                svc = GraylogConnector(base_url=connector.base_url, credentials=creds)
                msgs = await svc.search_messages(
                    'level:<=4 AND NOT source:(nsa* OR nss* OR nsc*)',
                    time_range_seconds=3600, limit=50,
                )
                raw_alerts.extend([{**m, "source": "graylog"} for m in msgs])
            elif connector.type == "wazuh":
                from app.services.connectors.wazuh import WazuhConnector
                svc = WazuhConnector(base_url=connector.base_url, credentials=creds)
                items = await svc.get_alerts(limit=50, min_level=7)
                raw_alerts.extend(items)
        except Exception as e:
            log.warning("collect_data: connector %s failed: %s", connector.type, e)

    # Also collect unread O365 mails
    result2 = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "o365",
            ConnectorConfig.enabled.is_(True),
        )
    )
    o365_conn = result2.scalars().first()
    if o365_conn:
        try:
            creds = decrypt_credentials(o365_conn.encrypted_credentials)
            from app.services.connectors.o365 import O365Connector
            svc = O365Connector(base_url=o365_conn.base_url, credentials=creds)
            mailbox = creds.get("mailbox", "")
            if mailbox:
                mails = await svc.get_unread_mails(mailbox, top=10)
                for mail in mails:
                    raw_alerts.append({
                        "source": "o365",
                        "severity": "medium" if mail.get("importance") != "high" else "high",
                        "title": mail.get("subject", ""),
                        "body": mail.get("preview", ""),
                    })
        except Exception as e:
            log.warning("collect_data: O365 failed: %s", e)

    # Apply CheckMK filters (location / ve / criticality) if configured
    from app.services.settings import get_agent_config
    config = await get_agent_config(db)

    # Collect active filter sets (user override already applied in run_sysadmin_workflow)
    loc_filter  = {v.lower() for v in (config.checkmk_locations          or [])}
    ve_filter   = {v.lower() for v in (state.get("_checkmk_ve")          or [])}
    crit_filter = {v.lower() for v in (state.get("_checkmk_criticality") or [])}
    os_filter   = {v.lower() for v in (state.get("_checkmk_os")          or [])}

    if loc_filter or ve_filter or crit_filter or os_filter:
        filtered = []
        for a in raw_alerts:
            if a.get("source") != "checkmk":
                filtered.append(a)
                continue
            meta = a.get("metadata") or {}
            loc  = meta.get("location",    "").lower()
            ve   = meta.get("ve",          "").lower()
            crit = meta.get("criticality", "").lower()
            os_v = meta.get("os",          "").lower()

            if loc_filter  and not any(f in loc  for f in loc_filter):
                continue
            if ve_filter   and not any(f in ve   for f in ve_filter):
                continue
            if crit_filter and not any(f in crit for f in crit_filter):
                continue
            if os_filter   and not any(f in os_v for f in os_filter):
                continue
            filtered.append(a)
        raw_alerts = filtered

    return {**state, "raw_alerts": raw_alerts}


# ─────────────────────────────────────────────────
# Node 2: enrich
# ─────────────────────────────────────────────────
async def enrich(state: dict, db: Any) -> dict:
    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "id_generator",
            ConnectorConfig.enabled.is_(True),
        )
    )
    idgen_conn = result.scalars().first()
    idgen_svc = None
    if idgen_conn:
        creds = decrypt_credentials(idgen_conn.encrypted_credentials)
        from app.services.connectors.id_generator import IDGeneratorConnector
        idgen_svc = IDGeneratorConnector(base_url=idgen_conn.base_url, credentials=creds)

    enriched: list[dict] = []
    for alert in state.get("raw_alerts", []):
        enriched_alert = dict(alert)
        host = alert.get("host") or alert.get("agent") or ""
        if idgen_svc and host:
            # Try to resolve host IP to location
            import socket
            try:
                ip = socket.gethostbyname(host)
                location = await idgen_svc.resolve_ip_to_location(ip)
                if location:
                    enriched_alert["location_name"] = location.get("location_name", "")
                    enriched_alert["location_city"] = location.get("location_city", "")
            except Exception:
                pass
        enriched.append(enriched_alert)

    return {**state, "enriched_alerts": enriched}


# ─────────────────────────────────────────────────
# Node 3: rag_lookup
# ─────────────────────────────────────────────────
async def rag_lookup(state: dict, db: Any, llm_config: Any, searxng_config: Any) -> dict:
    alerts = state.get("enriched_alerts", [])
    if not alerts:
        return {**state, "rag_context": []}

    rag_context: list[dict] = []

    # ── Step 0: Automatic server KB lookup ────────────────────────────────
    # For every affected host, fetch its Confluence inventory page from it-aikb.
    # These pages contain CheckMK custom checks, runbooks, and service details.
    # Done unconditionally — fast OpenSearch lookup, no LLM call.
    from sqlalchemy import select
    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == "it_aikb",
            ConnectorConfig.enabled.is_(True),
        )
    )
    aikb_row = result.scalars().first()
    aikb_svc = None
    if aikb_row:
        creds = decrypt_credentials(aikb_row.encrypted_credentials)
        from app.services.connectors.it_aikb import ITAikbConnector
        aikb_svc = ITAikbConnector(base_url=aikb_row.base_url, credentials=creds)

    if aikb_svc:
        # Extract unique short hostnames (strip domain, e.g. "docker0218.ippen.media" → "docker0218")
        unique_hosts: set[str] = set()
        for a in alerts:
            raw = (a.get("host") or a.get("agent") or "").strip()
            if raw:
                short = raw.split(".")[0].lower()
                if short:
                    unique_hosts.add(short)

        for host in list(unique_hosts)[:10]:   # cap to avoid excessive calls
            try:
                hits = await aikb_svc.search_opensearch(host, top_k=3)
                if hits:
                    rag_context.append({
                        "source": "server-kb",
                        "query": host,
                        "results": hits,
                    })
                    log.debug("rag_lookup: KB hit for host '%s' (%d chunks)", host, len(hits))
            except Exception as e:
                log.debug("rag_lookup: KB lookup for '%s' failed: %s", host, e)

    # Build events summary for LLM decision
    events_summary = "\n".join(
        f"- [{a.get('severity','?')}] {a.get('source','?')}: {a.get('title') or a.get('message','')[:100]}"
        for a in alerts[:20]
    )

    # ── Step 1: LLM decides if additional RAG lookup is needed ────────────
    try:
        decision_prompt = RAG_DECISION_PROMPT.format(events_summary=events_summary)
        decision_raw = await generate_text(
            llm_config,
            [{"role": "user", "content": decision_prompt}],
            reasoning_effort="low",
        )
        decision = json.loads(decision_raw)
    except Exception as e:
        log.warning("rag_lookup: LLM decision failed: %s", e)
        # Server KB context was already collected — don't discard it
        return {**state, "rag_context": rag_context}

    queries = decision.get("queries", [])
    use_deepsearch = decision.get("deepsearch", False)

    if not decision.get("needs_rag") or not queries:
        return {**state, "rag_context": rag_context}

    # ── Step 2: LLM-driven it-aikb search ─────────────────────────────────
    if aikb_svc:
        for query in queries:
            try:
                if use_deepsearch:
                    results = await aikb_svc.deepsearch(query)
                    rag_context.append({"source": "aikb-deepsearch", "query": query, "results": results})
                else:
                    results = await aikb_svc.search(query)
                    rag_context.append({"source": "aikb-standard", "query": query, "results": results})
            except Exception as e:
                log.warning("rag_lookup: aikb failed for query '%s': %s", query, e)

    # Step 3: SearXNG web search via HyDE pattern
    if searxng_config.is_configured and queries:
        for query in queries[:2]:  # limit web searches
            try:
                # Generate hypothetical answer (HyDE)
                hyde_prompt = SEARXNG_HYDE_PROMPT.format(problem=query)
                hypothetical_answer = await generate_text(
                    llm_config,
                    [{"role": "user", "content": hyde_prompt}],
                    reasoning_effort="low",
                )

                # Use the hypothetical answer as the search query for SearXNG
                import httpx
                async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                    r = await client.get(
                        f"{searxng_config.base_url}/search",
                        params={
                            "q": hypothetical_answer[:200],
                            "format": "json",
                            "categories": "general,it",
                            "language": "en",
                        },
                    )
                    if r.status_code == 200:
                        results = r.json().get("results", [])[:searxng_config.results_count]
                        rag_context.append({
                            "source": "searxng",
                            "query": query,
                            "hyde_query": hypothetical_answer[:100],
                            "results": [{"title": x.get("title"), "url": x.get("url"), "content": x.get("content", "")[:300]} for x in results],
                        })
            except Exception as e:
                log.warning("rag_lookup: SearXNG failed for query '%s': %s", query, e)

    return {**state, "rag_context": rag_context}


# ─────────────────────────────────────────────────
# Node 4: analyze
# ─────────────────────────────────────────────────
async def analyze(state: dict, llm_config: Any) -> dict:
    alerts = state.get("enriched_alerts", [])
    if not alerts:
        result = AnalysisResult(severity_summary="none")
        return {**state, "analysis": result.model_dump()}

    # Build context string
    def _alert_location(a: dict) -> str:
        loc = a.get("location_name") or (a.get("metadata") or {}).get("location", "")
        city = a.get("location_city", "")
        if loc and city and city.lower() not in loc.lower():
            return f"{loc} ({city})"
        return loc or city

    alerts_text = "\n".join(
        f"[{a.get('severity','?').upper()}] [{a.get('source','?')}] "
        f"{a.get('host') or a.get('agent') or ''}: "
        f"{a.get('title') or a.get('message','')[:200]}"
        + (f" | Standort: {_alert_location(a)}" if _alert_location(a) else "")
        + (f" | Ordner: {(a.get('metadata') or {}).get('location','')}" if (a.get('metadata') or {}).get('location') else "")
        for a in alerts[:40]
    )

    # Separate server KB context from other RAG context so the LLM knows what it's reading
    kb_text = ""
    rag_text = ""
    for ctx in state.get("rag_context", []):
        results = ctx.get("results", [])[:3]
        if not results:
            continue
        if ctx["source"] == "server-kb":
            kb_text += f"\n\nServer-Inventar für Host '{ctx['query']}' (Confluence KB):\n"
            for r in results:
                title = r.get("title") or ""
                content = r.get("content") or r.get("text") or ""
                url = r.get("source_url") or r.get("url") or ""
                url_part = f" (URL: {url})" if url else ""
                kb_text += f"- {title}{url_part}: {content[:400]}\n"
        else:
            rag_text += f"\n\nKontext aus {ctx['source']} für '{ctx['query']}':\n"
            for r in results:
                title = r.get("title") or r.get("source_id") or ""
                content = r.get("content") or r.get("text") or ""
                url = r.get("source_url") or r.get("url") or ""
                url_part = f" (URL: {url})" if url else ""
                rag_text += f"- {title}{url_part}: {content[:200]}\n"

    user_content = f"IT-Ereignisse der letzten Stunde:\n{alerts_text}"
    if kb_text:
        user_content += f"\n\nServer-Inventar aus Confluence (CheckMK-Checks, Runbooks):{kb_text}"
    if rag_text:
        user_content += f"\n\nWissensdatenbank-Kontext:{rag_text}"

    try:
        t0 = time.time()
        raw = await generate_text(
            llm_config,
            [
                {"role": "system", "content": SYSADMIN_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            reasoning_effort="medium",
        )
        duration = time.time() - t0

        raw = raw.strip()
        # Strip possible markdown code fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)

        result = AnalysisResult(
            severity_summary=parsed.get("severity_summary", "none"),
            findings=[Finding(**f) for f in parsed.get("findings", [])],
            recommendations=[Recommendation(**r) for r in parsed.get("recommendations", [])],
            rag_queries_used=state.get("rag_context", []),
            token_usage={},
        )
        log.info("analyze: severity=%s, findings=%d, duration=%.1fs",
                 result.severity_summary, len(result.findings), duration)
    except Exception as e:
        log.error("analyze: failed: %s", e)
        result = AnalysisResult(severity_summary="none", error=str(e))

    return {**state, "analysis": result.model_dump()}


# ─────────────────────────────────────────────────
# Node 5: act
# ─────────────────────────────────────────────────
async def act(state: dict, db: Any) -> dict:
    analysis_data = state.get("analysis", {})
    if not analysis_data:
        return state

    analysis = AnalysisResult(**analysis_data)

    # Persist to ai_analyses table
    from app.models.ai import AiAnalysis
    record = AiAnalysis(
        agent_type="sysadmin",
        sources_checked={"alert_count": len(state.get("raw_alerts", []))},
        findings=[f.model_dump() for f in analysis.findings],
        recommendations=[r.model_dump() for r in analysis.recommendations],
        severity_summary=analysis.severity_summary,
        rag_queries_used=analysis.rag_queries_used,
        token_usage=analysis.token_usage,
    )
    db.add(record)

    # Auto-create Jira tickets for critical/high recommendations
    auto_jira = state.get("auto_jira", True)
    threshold = state.get("jira_threshold", "critical")
    jira_project = state.get("jira_project", "IMIT")
    threshold_levels = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    min_level = threshold_levels.get(threshold, 0)

    tickets_created: list[str] = []
    if auto_jira:
        from sqlalchemy import select as sa_select
        from app.core.security import decrypt_credentials
        from app.models.connector import ConnectorConfig

        result = await db.execute(
            sa_select(ConnectorConfig).where(
                ConnectorConfig.type.in_(["jira", "jira_sd"]),
                ConnectorConfig.enabled.is_(True),
            )
        )
        jira_conn = result.scalars().first()
        if jira_conn:
            from app.services.connectors.jira import JiraConnector
            creds = decrypt_credentials(jira_conn.encrypted_credentials)
            jira_project = creds.get("project", jira_project)
            jira_svc = JiraConnector(base_url=jira_conn.base_url, credentials=creds)

            priority_map = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}
            for rec in analysis.recommendations:
                rec_level = threshold_levels.get(rec.priority, 3)
                if rec_level > min_level:
                    continue
                title = rec.jira_title or rec.action[:200]
                try:
                    existing = await jira_svc.issue_exists_by_summary(jira_project, title)
                    if existing:
                        tickets_created.append(existing)
                        continue
                    issue = await jira_svc.create_issue(
                        project=jira_project,
                        summary=title,
                        description=f"{rec.rationale}\n\nAction: {rec.action}",
                        issue_type="Bug",
                        priority=priority_map.get(rec.priority, "High"),
                        labels=["CentralStation", "AI-generated"],
                    )
                    tickets_created.append(issue.get("key", "?"))
                except Exception as e:
                    log.warning("act: Jira ticket creation failed: %s", e)

    record.jira_tickets_created = tickets_created
    await db.commit()
    await db.refresh(record)

    # Push via WebSocket to sysadmin/admin clients
    try:
        from app.api.ws import manager
        await manager.broadcast(
            {
                "type": "ai_insight",
                "severity": analysis.severity_summary,
                "findings_count": len(analysis.findings),
                "recommendations_count": len(analysis.recommendations),
                "jira_tickets": tickets_created,
                "analysis_id": str(record.id),
            },
            roles=["admin", "sysadmin"],
        )
    except Exception as e:
        log.warning("act: WS broadcast failed: %s", e)

    return {**state, "jira_tickets_created": tickets_created}


# ─────────────────────────────────────────────────
# Build the graph
# ─────────────────────────────────────────────────
def build_sysadmin_graph():
    """Returns the compiled LangGraph workflow (no DB/config injected yet)."""
    graph = StateGraph(dict)
    graph.add_node("collect_data", lambda s: s)
    graph.add_node("enrich", lambda s: s)
    graph.add_node("rag_lookup", lambda s: s)
    graph.add_node("analyze", lambda s: s)
    graph.add_node("act", lambda s: s)
    graph.set_entry_point("collect_data")
    graph.add_edge("collect_data", "enrich")
    graph.add_edge("enrich", "rag_lookup")
    graph.add_edge("rag_lookup", "analyze")
    graph.add_edge("analyze", "act")
    graph.add_edge("act", END)
    return graph.compile()


async def run_sysadmin_workflow(
    db: Any,
    user_checkmk_locations:   list[str] | None = None,
    user_checkmk_ve:          list[str] | None = None,
    user_checkmk_criticality: list[str] | None = None,
    user_checkmk_os:          list[str] | None = None,
) -> dict:
    """Run the full sysadmin agent workflow.

    user_checkmk_* lists come from the triggering user's preferences and
    override the global agent.checkmk_locations setting.
    """
    from app.services.settings import get_agent_config, get_llm_config, get_searxng_config

    llm_config = await get_llm_config(db)
    searxng_config = await get_searxng_config(db)
    agent_config = await get_agent_config(db)

    if not llm_config.is_configured:
        log.warning("run_sysadmin_workflow: LLM not configured, skipping")
        return {}

    # User preference overrides global location setting
    if user_checkmk_locations is not None:
        agent_config.checkmk_locations = user_checkmk_locations or None

    state: dict = {
        "raw_alerts": [],
        "enriched_alerts": [],
        "rag_context": [],
        "analysis": None,
        "jira_project": "IMIT",
        "auto_jira": agent_config.auto_jira,
        "jira_threshold": agent_config.jira_severity_threshold,
        # pass user ve/criticality/os filters via state (read in collect_data)
        "_checkmk_ve":          user_checkmk_ve          or [],
        "_checkmk_criticality": user_checkmk_criticality or [],
        "_checkmk_os":          user_checkmk_os          or [],
    }

    state = await collect_data(state, db)
    if not state["raw_alerts"]:
        log.info("run_sysadmin_workflow: no alerts found")
        return state
    state = await enrich(state, db)
    state = await rag_lookup(state, db, llm_config, searxng_config)
    state = await analyze(state, llm_config)
    state = await act(state, db)
    return state
