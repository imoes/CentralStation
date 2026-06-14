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
    """Read persisted alerts from DB instead of calling connectors directly.

    This ensures the AI analyses only open, deduplicated, persistent problems:
    - status='new' (not acknowledged or resolved)
    - at least min_age_minutes old (transient events already gone)
    - created within look_back_hours (not ancient history)
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import and_, select
    from app.models.alert import Alert

    now = datetime.now(timezone.utc)
    look_back_hours = state.get("look_back_hours", 4)
    min_age_minutes = state.get("min_age_minutes", 10)
    since      = now - timedelta(hours=look_back_hours)
    max_age_ts = now - timedelta(minutes=min_age_minutes)

    result = await db.execute(
        select(Alert)
        .where(
            and_(
                Alert.status == "new",
                Alert.created_at >= since,
                Alert.created_at <= max_age_ts,
            )
        )
        .order_by(Alert.created_at.desc())
        .limit(200)   # fetch wider set; scoring trims to max_alerts_for_llm
    )
    db_alerts = result.scalars().all()

    # Split DB alerts: non-CheckMK stays (dedup-critical for Graylog/Wazuh/Coroot),
    # CheckMK is replaced by a live fetch below (fresher state, bypasses 10-min cache).
    _CHECKMK_SEV: dict[str, str] = {"warning": "high", "critical": "critical", "unknown": "medium"}

    db_non_checkmk: list[dict] = []
    db_checkmk_fallback: list[dict] = []
    for a in db_alerts:
        meta = a.metadata_ or {}
        entry = {
            "source":        a.source,
            "severity":      a.severity,
            "title":         a.title,
            "body":          a.body or "",
            "external_id":   a.external_id,
            "host":          meta.get("host") or meta.get("agent") or meta.get("container_name") or "",
            "agent":         meta.get("agent") or "",
            "metadata":      meta,
            "location_name": a.location_name or "",
            "location_city": a.location_city or "",
        }
        if a.source == "checkmk":
            db_checkmk_fallback.append(entry)
        else:
            db_non_checkmk.append(entry)

    # Try fresh CheckMK live fetch; fall back to DB on any error.
    live_checkmk: list[dict] = []
    try:
        from app.services.settings import get_active_checkmk_connector
        cmk = await get_active_checkmk_connector(db)
        if cmk:
            problems = await cmk.get_problems(time_range_minutes=int(look_back_hours * 60))
            for p in problems:
                lsc = p.get("last_state_change")
                # Skip problems that are too fresh (likely transient) — mirrors min_age_minutes.
                if lsc and (now.timestamp() - float(lsc)) < (min_age_minutes * 60):
                    continue
                meta = dict(p.get("metadata") or {})
                meta.setdefault("host", p.get("host", ""))
                meta["service"] = p.get("service")
                meta["output"] = p.get("output")
                meta["last_state_change"] = lsc
                live_checkmk.append({
                    "source":        "checkmk",
                    "severity":      _CHECKMK_SEV.get(p.get("severity", ""), "medium"),
                    "title":         f"{p.get('host','')}: {p.get('service','')} {(p.get('severity') or '').upper()}",
                    "body":          p.get("output", ""),
                    "host":          p.get("host", ""),
                    "agent":         "",
                    "metadata":      meta,
                    "location_name": "",
                    "location_city": "",
                })
            log.info("collect_data: fetched %d CheckMK problems live", len(live_checkmk))
    except Exception as e:
        log.warning("collect_data: live CheckMK fetch failed, using DB: %s", e)
        live_checkmk = db_checkmk_fallback

    if not live_checkmk:
        live_checkmk = db_checkmk_fallback

    raw_alerts: list[dict] = live_checkmk + db_non_checkmk

    # Load global agent settings as fallback for filters not provided via state
    from app.services.settings import get_agent_config
    cfg = await get_agent_config(db)

    # User preference (passed via state) overrides global location setting.
    # VE/criticality/OS only come from user preferences — no global default for those.
    loc_filter  = {v.lower() for v in (state.get("_checkmk_locations")   or cfg.checkmk_locations or [])}
    ve_filter   = {v.lower() for v in (state.get("_checkmk_ve")          or [])}
    crit_filter = {v.lower() for v in (state.get("_checkmk_criticality") or [])}
    os_filter   = {v.lower() for v in (state.get("_checkmk_os")          or [])}
    host_scope  = {v.lower() for v in (state.get("_checkmk_host_scope")  or [])}

    import re
    _SWITCH_RE = re.compile(r'^ns[asc]\d', re.IGNORECASE)

    # Apply the same exclusion searches the feed/worklist use, so the agent never
    # analyses (and reports) noise the operator already excluded (e.g. promiscuous mode).
    from app.services.feed_index import get_exclusion_matchers, matches_exclusion
    exclusion_matchers = await get_exclusion_matchers(db)

    filtered = []
    for a in raw_alerts:
        source = a["source"]
        host = a.get("host") or a.get("agent") or ""

        # Exclude Graylog switch messages (nsa*/nss*/nsc* hosts) — these belong to the
        # Network-Technician agent, not the SysAdmin analysis.
        if source == "graylog" and _SWITCH_RE.match(host):
            continue

        # Skip alerts matching an active exclusion rule (body/title, AND/OR aware)
        if exclusion_matchers and matches_exclusion(f"{a.get('title','')} {a.get('body','')}", exclusion_matchers):
            continue

        if source != "checkmk":
            if host_scope and host.lower() not in host_scope:
                continue
            filtered.append(a)
            continue

        # Apply CheckMK-specific filters
        meta = a["metadata"]
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

    # ── CPU-scoring: trim to max_alerts_for_llm ───────────────────────────────
    max_for_llm = state.get("max_alerts_for_llm", 30)
    scoring_enabled = state.get("scoring_enabled", True)
    if len(raw_alerts) > max_for_llm and scoring_enabled:
        try:
            from app.services.alert_scorer import score_alerts_batch
            scored = await score_alerts_batch(
                raw_alerts, db,
                min_age_minutes=min_age_minutes,
                flap_window_minutes=state.get("flap_window_minutes", 30),
                flap_threshold=state.get("flap_threshold", 3),
            )
            raw_alerts = [a for _, a in scored[:max_for_llm]]
            log.info(
                "collect_data: scored %d → top %d alerts for LLM",
                len(filtered), len(raw_alerts),
            )
        except Exception as e:
            log.debug("collect_data: scoring failed, using first %d: %s", max_for_llm, e)
            raw_alerts = raw_alerts[:max_for_llm]
            try:
                await db.rollback()
            except Exception:
                pass

    log.info(
        "collect_data: %d open alerts (look_back=%dh, min_age=%dmin)",
        len(raw_alerts), look_back_hours, min_age_minutes,
    )

    # Load past incidents for all unique hosts in this batch
    past_incidents: list[dict] = []
    try:
        from app.services.ai_agent.past_incidents import find_similar_incidents
        seen_hosts: set[str] = set()
        for a in raw_alerts:
            h = a.get("host") or a.get("metadata", {}).get("host", "")
            if h and h not in seen_hosts:
                seen_hosts.add(h)
                inc = await find_similar_incidents(h, db, limit=2)
                past_incidents.extend(inc)
                if len(past_incidents) >= 6:
                    break
    except Exception as e:
        log.debug("collect_data: past_incidents lookup failed: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass

    return {**state, "raw_alerts": raw_alerts, "past_incidents": past_incidents}


# ─────────────────────────────────────────────────
# Node 2: enrich
# ─────────────────────────────────────────────────
async def enrich(state: dict, db: Any) -> dict:
    log.info("agent node: enrich (%d alerts)", len(state.get("raw_alerts", [])))
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
    log.info("agent node: rag_lookup (%d enriched alerts)", len(state.get("enriched_alerts", [])))
    alerts = state.get("enriched_alerts", [])
    if not alerts:
        return {**state, "rag_context": []}

    rag_context: list[dict] = []

    # Extract unique hostnames for metrics lookups
    unique_hosts: set[str] = set()
    for a in alerts:
        raw = (a.get("host") or a.get("agent") or "").strip()
        if raw:
            unique_hosts.add(raw)

    # ── Step 0: Recent metrics from cs-metrics-checkmk ───────────────────────
    # For hosts with critical/high alerts, pull recent metric snapshots so the
    # LLM can correlate "CPU was 94% → OOM → container restart" in one context.
    critical_hosts = {
        (a.get("host") or a.get("agent") or "").strip()
        for a in alerts
        if a.get("severity") in ("critical", "high") and (a.get("host") or a.get("agent"))
    }
    if critical_hosts:
        from app.services.metrics_collector import query_metrics_for_host
        for host in list(critical_hosts)[:5]:
            try:
                metrics = await query_metrics_for_host(host, hours=2)
                if metrics:
                    # Format as compact text for LLM context
                    snippets = [
                        f"{m['service']}/{m['metric']}: {m['value']:.2f}{m.get('unit','')} @ {m['timestamp'][:16]}"
                        for m in metrics[:20]
                    ]
                    rag_context.append({
                        "source": "checkmk-metrics",
                        "query": host,
                        "results": [{"title": f"Aktuelle Metriken {host}", "content": "\n".join(snippets)}],
                    })
                    log.debug("rag_lookup: %d metric points for host '%s'", len(metrics), host)
            except Exception as e:
                log.debug("rag_lookup: metrics for '%s' failed: %s", host, e)

    # ── Step 0b: Recent Graylog logs for Graylog-affected hosts ─────────────────
    # Pull the last 5 raw log lines per host to give the LLM concrete evidence
    # beyond the single deduplicated alert text (max 5 hosts × 5 lines × 200 chars).
    graylog_hosts = {
        (a.get("host") or a.get("agent") or "").strip()
        for a in alerts
        if a.get("source") == "graylog" and (a.get("host") or a.get("agent") or "").strip()
    }
    if graylog_hosts:
        from app.services.feed_index import search_by_query
        for gh in list(graylog_hosts)[:5]:
            try:
                recent_logs = await search_by_query(
                    index_pattern="cs-feed-graylog",
                    query_string=f'metadata.host:"{gh}" OR metadata.container_name:"{gh}"',
                    size=5,
                )
                if recent_logs:
                    log_lines = [
                        (l.get("body") or l.get("title") or "")[:200]
                        for l in recent_logs
                        if (l.get("body") or l.get("title"))
                    ]
                    if log_lines:
                        rag_context.append({
                            "source": "graylog-logs",
                            "query": gh,
                            "results": [{"title": f"Letzte Graylog-Logs für {gh}", "content": "\n".join(log_lines)}],
                        })
                        log.debug("rag_lookup: %d recent log lines for host '%s'", len(log_lines), gh)
            except Exception as e:
                log.debug("rag_lookup: graylog logs for '%s' failed: %s", gh, e)

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

    # ── Step 1b: IT-AIKB internal KB lookup ──────────────────────────────────
    # Query the Confluence knowledge base for documentation, runbooks, and
    # dependency graphs relevant to the current alerts before hitting the web.
    try:
        from sqlalchemy import select
        from app.models.connector import ConnectorConfig
        from app.core.security import decrypt_credentials
        from app.services.connectors.aikb import AIKBConnector

        r = await db.execute(
            select(ConnectorConfig)
            .where(ConnectorConfig.type == "aikb", ConnectorConfig.enabled.is_(True))
            .limit(1)
        )
        aikb_conn = r.scalar_one_or_none()
        if aikb_conn:
            creds = decrypt_credentials(aikb_conn.encrypted_credentials)
            aikb = AIKBConnector(base_url=aikb_conn.base_url, credentials=creds)

            for query in queries[:2]:
                if use_deepsearch:
                    # Full LLM-powered answer for complex queries
                    result = await aikb.search_rag(query, deepsearch=True)
                    answer = result.get("answer", "")
                    hits = result.get("results", [])
                    if answer or hits:
                        rag_context.append({
                            "source": "server-kb",
                            "query": query,
                            "results": [
                                {"title": "IT-AIKB Antwort", "content": answer[:600], "source_url": ""},
                                *hits[:3],
                            ],
                        })
                        log.debug("rag_lookup: AIKB deepsearch for '%s' → %d chars answer", query, len(answer))
                else:
                    # Fast OpenSearch hits
                    hits = await aikb.search_opensearch(query, size=4)
                    if hits:
                        rag_context.append({
                            "source": "server-kb",
                            "query": query,
                            "results": hits,
                        })
                        log.debug("rag_lookup: AIKB opensearch for '%s' → %d hits", query, len(hits))
    except Exception as e:
        log.warning("rag_lookup: AIKB lookup failed: %s", e)

    # ── Step 2: SearXNG web search via HyDE pattern
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
    log.info("agent node: analyze (model=%s)", getattr(llm_config, "model", "?"))
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

    def _alert_host(a: dict) -> str:
        meta = a.get("metadata") or {}
        return str(a.get("host") or a.get("agent") or meta.get("host") or meta.get("container_name") or "").strip()

    all_hosts = sorted({h for h in (_alert_host(a) for a in alerts) if h}, key=str.lower)

    alerts_text = "\n".join(
        f"[{a.get('severity','?').upper()}] [{a.get('source','?')}] "
        f"{_alert_host(a)}: "
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

    # ── Blast-Radius: topological context for critical alerts ─────────────────
    blast_text = ""
    try:
        from app.services.incident.blast_radius import get_blast_radius_for_alerts
        br_results = await get_blast_radius_for_alerts(alerts, db)
        if br_results:
            lines = []
            for br in br_results:
                line = f"Host {br['host']}"
                if br.get("location"):
                    line += f" (Standort: {br['location']})"
                if br.get("co_hosted_vms"):
                    line += f" | Ko-lokalisierte VMs: {', '.join(br['co_hosted_vms'][:5])}"
                if br.get("co_located_hosts"):
                    line += f" | Weitere Hosts am Standort: {', '.join(br['co_located_hosts'][:5])}"
                lines.append(line)
            blast_text = "\n\nBlast-Radius (betroffene Topologie):\n" + "\n".join(lines)
    except Exception as e:
        log.debug("analyze: blast_radius failed: %s", e)

    # ── Past-Incidents context ─────────────────────────────────────────────────
    past_text = ""
    try:
        from app.services.ai_agent.past_incidents import format_past_incidents_for_llm
        past_incidents = state.get("past_incidents", [])
        if past_incidents:
            past_text = "\n\n" + format_past_incidents_for_llm(past_incidents)
    except Exception as e:
        log.debug("analyze: past_incidents format failed: %s", e)

    user_content = f"IT-Ereignisse der letzten Stunde:\n{alerts_text}"
    if all_hosts:
        user_content += "\n\nBetroffene Hosts vollständig:\n" + ", ".join(all_hosts)
        user_content += "\n\nWichtig: Jeder Host aus dieser vollständigen Liste muss im Ergebnis namentlich auftauchen."
    if past_text:
        user_content += past_text
    if blast_text:
        user_content += blast_text
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
    log.info("agent node: act")
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

        # Use the configured ticket project + connector (IMIT lives in jira_sd).
        from app.services.settings import get_setting
        jira_project = (await get_setting(db, "jira.ticket_project")) or jira_project
        target_type = (await get_setting(db, "jira.ticket_connector")) or "jira_sd"
        # Prefer the configured type, fall back to the other one.
        jira_conn = None
        for ctype in (target_type, "jira" if target_type == "jira_sd" else "jira_sd"):
            result = await db.execute(
                sa_select(ConnectorConfig).where(
                    ConnectorConfig.type == ctype,
                    ConnectorConfig.enabled.is_(True),
                )
            )
            jira_conn = result.scalars().first()
            if jira_conn:
                break
        if jira_conn:
            from app.services.connectors.jira import JiraConnector
            creds = decrypt_credentials(jira_conn.encrypted_credentials)
            jira_svc = JiraConnector(base_url=jira_conn.base_url, credentials=creds)

            # ServiceDesk (IMIT) uses German priority names + Serviceanfrage type.
            priority_map = {"critical": "Kritisch", "high": "Hoch", "medium": "Normal", "low": "Niedrig"}
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
                        issue_type="Serviceanfrage",
                        priority=priority_map.get(rec.priority, "Hoch"),
                        labels=["CentralStation", "AI-generated"],
                    )
                    key = issue.get("key", "?")
                    tickets_created.append(key)
                    log.info("act: Jira ticket created: %s — %s", key, title[:80])
                    # Adaptive learning: this alert pattern led to a ticket → boost
                    try:
                        from app.services.alert_score_learner import record_jira_created
                        # Find the alert in enriched_alerts that matches this finding
                        for a in state.get("enriched_alerts", []):
                            if rec.action and (a.get("host", "") in rec.action or a.get("title", "") in title):
                                await record_jira_created(a, db)
                                break
                    except Exception:
                        pass
                except Exception as e:
                    log.warning("act: Jira ticket creation failed: %s", e)

    record.jira_tickets_created = tickets_created
    await db.commit()
    await db.refresh(record)

    # Propose AWX remediations for critical/high findings
    remediation_proposals: list[str] = []
    try:
        from app.services.remediation_matcher import propose_remediation
        # Map host → external_id from the analyzed alerts so the learning loop
        # (which keys on external_id) can write back to OpenSearch/AlertComment.
        host_to_eid: dict[str, str] = {}
        for a in state.get("enriched_alerts", []):
            h = (a.get("host") or a.get("agent") or "").strip()
            eid = a.get("external_id")
            if h and eid and h not in host_to_eid:
                host_to_eid[h] = eid
        for finding in analysis.findings:
            if getattr(finding, "severity", "low") not in ("critical", "high"):
                continue
            f_host = getattr(finding, "host", "") or ""
            proposal = await propose_remediation(
                finding_title=getattr(finding, "title", str(finding)[:200]),
                rationale=getattr(finding, "description", ""),
                host=f_host,
                external_id=host_to_eid.get(f_host.strip()),
                analysis_id=record.id,
                db=db,
            )
            if proposal:
                remediation_proposals.append(str(proposal.id))
        if remediation_proposals:
            await db.commit()
    except Exception as exc:
        log.warning("act: remediation matcher failed: %s", exc)

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
                "remediation_proposals": remediation_proposals,
            },
            roles=["admin", "sysadmin"],
        )
        if remediation_proposals:
            await manager.broadcast(
                {"type": "remediation_proposed", "ids": remediation_proposals},
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
    user_checkmk_host_scope:  list[str] | None = None,
    min_age_minutes: int = 10,
    look_back_hours: int = 4,
) -> dict:
    """Run the full sysadmin agent workflow.

    Reads persisted alerts from the DB (not raw connector data).
    Only includes open alerts that are at least min_age_minutes old —
    filtering out transient events that already resolved themselves.

    user_checkmk_* lists come from the triggering user's preferences.
    """
    from app.services.settings import get_agent_config, get_active_llm_config as get_llm_config, get_searxng_config

    llm_config = await get_llm_config(db)
    searxng_config = await get_searxng_config(db)
    agent_config = await get_agent_config(db)

    if not llm_config.is_configured:
        log.warning("run_sysadmin_workflow: LLM not configured, skipping")
        return {}

    state: dict = {
        "raw_alerts": [],
        "enriched_alerts": [],
        "rag_context": [],
        "analysis": None,
        "jira_project": "IMIT",
        "auto_jira": agent_config.auto_jira,
        "jira_threshold": agent_config.jira_severity_threshold,
        "min_age_minutes": min_age_minutes,
        "look_back_hours": look_back_hours,
        # CheckMK user filters passed into collect_data
        "_checkmk_locations":   user_checkmk_locations   or [],
        "_checkmk_ve":          user_checkmk_ve          or [],
        "_checkmk_criticality": user_checkmk_criticality or [],
        "_checkmk_os":          user_checkmk_os          or [],
        "_checkmk_host_scope":  user_checkmk_host_scope  or [],
        # Scoring settings
        "max_alerts_for_llm":  agent_config.max_alerts_for_llm,
        "flap_window_minutes": agent_config.flap_window_minutes,
        "flap_threshold":      agent_config.flap_threshold,
        "scoring_enabled":     agent_config.scoring_enabled,
    }

    state = await collect_data(state, db)
    if not state["raw_alerts"]:
        log.info("run_sysadmin_workflow: no alerts found")
        return state
    state = await enrich(state, db)
    if agent_config.rag_enabled:
        state = await rag_lookup(state, db, llm_config, searxng_config)
    state = await analyze(state, llm_config)
    state = await act(state, db)
    return state
