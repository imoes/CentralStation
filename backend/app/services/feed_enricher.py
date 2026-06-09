"""Feed Enricher — adds a short AI explanation to new feed alerts.

Called from alert_aggregator after indexing new alerts.
Optionally uses SearXNG web search to add context before the LLM explanation.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_ENRICH_SEVERITIES = {"critical", "high", "warning"}
_MAX_CONCURRENT = 5  # parallel LLM calls

_EXPLAIN_SYSTEM_PROMPT = (
    "You are an experienced Linux sysadmin. "
    "Explain the following monitoring message in 3-4 complete sentences: what it means, "
    "the likely cause, and the first concrete action. "
    "No Markdown formatting. Do not cut off mid-sentence.\n"
    "IMPORTANT: the 'log source' field indicates which monitoring tool COLLECTED the message "
    "(e.g. Graylog, CheckMK, Wazuh) — NOT which software has the problem. "
    "Identify the affected system from the message content (hostname, error text, process).\n"
    "EVIDENCE REQUIRED: if the message lacks sufficient data, state explicitly "
    "'Insufficient data for a root-cause analysis.' "
    "Do not invent causes, misconfigurations or remediation steps that do not follow "
    "directly from the message content."
)


async def _web_search(query: str, searxng_url: str, results_count: int = 5) -> str:
    """Run a SearXNG web search and return formatted snippets."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.get(
                f"{searxng_url}/search",
                params={"q": query, "format": "json"},
            )
            if r.status_code == 200:
                results = [
                    f"- {x.get('title')}: {x.get('content', '')[:200]}"
                    for x in r.json().get("results", [])[:results_count]
                ]
                return "\n".join(results)
    except Exception as e:
        log.debug("Web search failed: %s", e)
    return ""


async def _enrich_one(item: dict, llm, searxng_url: str = "") -> str | None:
    """Generate and store an AI insight for a single feed item. Returns the insight text."""
    from app.core.opensearch import get_opensearch
    from app.services.feed_index import _index  # type: ignore[attr-defined]

    meta = item.get("metadata") or {}
    host = meta.get("host", "")
    location = meta.get("location", "")
    application = meta.get("application", "")
    log_file_path = meta.get("log_file_path", "")
    source = item.get("source", "")
    title = item.get("title", "")
    body = (item.get("body") or "")[:300]

    # Build human-readable content — label source as collector, not broken system
    source_label = {
        "graylog": "Graylog (Log-Aggregator)",
        "wazuh":   "Wazuh (Security-Monitoring)",
        "checkmk": "CheckMK (Infrastruktur-Monitoring)",
    }.get(source.lower(), source.upper())

    user_content = f"Log-Quelle: {source_label}\n"
    if host:
        user_content += f"Betroffener Host: {host}\n"
    if application:
        user_content += f"Applikation/Dienst: {application}\n"
    if log_file_path:
        user_content += f"Logdatei: {log_file_path}\n"
    if location:
        user_content += f"Standort: {location}\n"
    user_content += f"\nMeldung: {title}"
    if body:
        user_content += f"\nDetails: {body}"

    # Web search: use host + title keywords, not the raw log source name
    if searxng_url:
        web_query = f"{host} {title[:80]}" if host else title[:100]
        web_snippet = await _web_search(web_query, searxng_url)
        if web_snippet:
            user_content += f"\n\nWeb-Suchergebnisse:\n{web_snippet}"

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        # Respond in the operator's configured UI language (default English).
        from app.services.ai_language import language_instruction, DEFAULT_LANG
        lang = item.get("_response_lang") or DEFAULT_LANG
        system_content = f"{_EXPLAIN_SYSTEM_PROMPT}\n{language_instruction(lang)}"
        response = await llm.ainvoke([
            SystemMessage(content=system_content),
            HumanMessage(content=user_content),
        ])
        insight = (response.content or "").strip()[:1200]
        if not insight:
            return None

        doc_id = item.get("id") or item.get("external_id")
        if not doc_id:
            return insight

        os_client = get_opensearch()
        await os_client.update(
            index=_index(source),
            id=str(doc_id),
            body={"doc": {"ai_insight": insight}},
        )

        # Persist insight as AlertComment so the Computer Console and incident
        # workflows can reference it later via get_alert_analysis().
        external_id = item.get("external_id")
        if external_id:
            try:
                import uuid as _uuid
                from app.core.database import AsyncSessionLocal
                from app.models.workflow import AlertComment
                async with AsyncSessionLocal() as _db:
                    _db.add(AlertComment(
                        id=_uuid.uuid4(),
                        external_id=str(external_id),
                        user_id=None,
                        user_name="KI-Analyse",
                        kind="ai",
                        body=f"🤖 Automatische Analyse:\n{insight}",
                    ))
                    await _db.commit()
            except Exception as _ce:
                log.debug("Could not save enrichment comment for %s: %s", external_id, _ce)

        return insight
    except Exception as e:
        log.debug("Feed enrichment failed for %s: %s", item.get("id"), e)
        return None


def _build_llm(llm_config, timeout: int = 30):
    """Build a ChatOpenAI instance with optional thinking mode."""
    from langchain_openai import ChatOpenAI
    kwargs: dict = dict(
        base_url=llm_config.base_url,
        model=llm_config.model,
        api_key=llm_config.api_key or "none",
        max_tokens=450,
        timeout=timeout,
    )
    if getattr(llm_config, "thinking_mode", False):
        kwargs["model_kwargs"] = {"extra_body": {"enable_thinking": True, "thinking_budget": 512}}
    return ChatOpenAI(**kwargs)


async def enrich_single(item: dict, llm_config, searxng_url: str = "") -> str | None:
    """Enrich a single feed item on demand. Returns insight text or None."""
    if not llm_config or not llm_config.is_configured:
        return None
    try:
        llm = _build_llm(llm_config, timeout=60)
    except Exception as e:
        log.warning("Could not initialise LLM for on-demand enrichment: %s", e)
        return None
    # Stamp the configured response language so the insight matches the UI language.
    if "_response_lang" not in item:
        try:
            from app.core.database import AsyncSessionLocal
            from app.services.ai_language import get_response_language
            async with AsyncSessionLocal() as _db:
                item = {**item, "_response_lang": await get_response_language(_db)}
        except Exception:
            pass
    return await _enrich_one(item, llm, searxng_url=searxng_url)


async def enrich_batch(
    items: list[dict],
    llm_config,
    searxng_url: str = "",
    agent_cfg=None,
    db=None,
) -> None:
    """Enrich a batch of feed items with AI insights (best-effort, non-blocking).

    Applies CPU-based scoring before calling the LLM — only items above
    agent_cfg.enrich_score_threshold receive LLM analysis.
    """
    if not llm_config or not llm_config.is_configured:
        return

    # ── Score-based pre-filter ────────────────────────────────────────────────
    severity_candidates = [i for i in items if i.get("severity") in _ENRICH_SEVERITIES]
    if not severity_candidates:
        return

    scoring_on = not agent_cfg or getattr(agent_cfg, "scoring_enabled", True)

    if not scoring_on:
        # Beta bypass: scoring disabled — all severity-eligible items go to LLM
        log.info("feed_enricher: CPU scoring disabled — enriching all %d candidates", len(severity_candidates))
        targets = severity_candidates
    else:
        threshold   = getattr(agent_cfg, "enrich_score_threshold", 80) if agent_cfg else 80
        min_age     = getattr(agent_cfg, "interval_minutes", 10) if agent_cfg else 10
        flap_window = getattr(agent_cfg, "flap_window_minutes", 30) if agent_cfg else 30
        flap_thr    = getattr(agent_cfg, "flap_threshold", 3) if agent_cfg else 3
        try:
            from app.services.alert_scorer import score_alerts_batch
            scored  = await score_alerts_batch(
                severity_candidates, db,
                min_age_minutes=min_age,
                flap_window_minutes=flap_window,
                flap_threshold=flap_thr,
            )
            targets = [a for score, a in scored if score >= threshold]
            log.info(
                "feed_enricher: %d/%d items above score threshold %d",
                len(targets), len(severity_candidates), threshold,
            )
        except Exception as e:
            log.debug("feed_enricher: scoring failed, falling back: %s", e)
            targets = severity_candidates

    if not targets:
        return

    try:
        llm = _build_llm(llm_config)
    except Exception as e:
        log.warning("Could not initialise LLM for feed enrichment: %s", e)
        return

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _guarded(item: dict) -> None:
        async with sem:
            await _enrich_one(item, llm, searxng_url=searxng_url)

    await asyncio.gather(*[_guarded(i) for i in targets], return_exceptions=True)
