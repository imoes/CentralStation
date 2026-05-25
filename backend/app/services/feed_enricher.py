"""Feed Enricher — adds a short AI explanation to new feed alerts.

Called from alert_aggregator after indexing new alerts.
Uses HyDE (Hypothetical Document Embeddings) pattern:
  1. LLM generates a concise English search query for the log event
  2. it-aikb RAG is searched with that query (if connector is configured)
  3. LLM explains the alert WITH the RAG context as background knowledge

Ref: llm-graylog-analyse/graylog_analyzer.py:hyde_query_for_log + analyze_single_log
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_ENRICH_SEVERITIES = {"critical", "high", "warning"}
_MAX_CONCURRENT = 5  # parallel LLM calls

_HYDE_SYSTEM_PROMPT = (
    "You are a Linux sysadmin. Generate a concise English search query (max 12 words) "
    "to find the cause or solution for this monitoring event. "
    "Reply with ONLY the search query, nothing else."
)

_EXPLAIN_SYSTEM_PROMPT = (
    "Du bist ein erfahrener Linux-Sysadmin. "
    "Erkläre die folgende Monitoring-Meldung in 3-4 vollständigen Sätzen: Was bedeutet sie, "
    "was ist die wahrscheinliche Ursache, und was ist die erste konkrete Maßnahme? "
    "Antworte auf Deutsch. Keine Markdown-Formatierung. Kein Abschneiden mitten im Satz."
)


async def _hyde_rag_lookup(item: dict, llm, aikb_svc) -> str:
    """Generate a HyDE search query and look up it-aikb. Returns context snippet or ''."""
    source = item.get("source", "")
    title = item.get("title", "")
    body = (item.get("body") or "")[:200]

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        hyde_input = f"{source.upper()}: {title}"
        if body:
            hyde_input += f"\n{body}"
        hyde_resp = await llm.ainvoke([
            SystemMessage(content=_HYDE_SYSTEM_PROMPT),
            HumanMessage(content=hyde_input),
        ])
        hyde_query = (hyde_resp.content or "").strip().strip('"')[:150]
        if not hyde_query:
            return ""

        log.debug("HyDE query for '%s': %s", title[:60], hyde_query)
        hits = await aikb_svc.search_opensearch(hyde_query, top_k=2)
        if not hits:
            return ""

        snippets = []
        for h in hits:
            content = (h.get("content") or h.get("body") or h.get("text") or "")[:250]
            title_hit = h.get("title") or h.get("page_title") or ""
            if content:
                snippets.append(f"- {title_hit}: {content}" if title_hit else f"- {content}")
        return "\n".join(snippets)
    except Exception as e:
        log.debug("HyDE RAG lookup failed: %s", e)
        return ""


async def _enrich_one(item: dict, llm, aikb_svc=None) -> str | None:
    """Generate and store an AI insight for a single feed item. Returns the insight text."""
    from app.core.opensearch import get_opensearch
    from app.services.feed_index import _index  # type: ignore[attr-defined]

    host = (item.get("metadata") or {}).get("host", "")
    location = (item.get("metadata") or {}).get("location", "")
    source = item.get("source", "")
    title = item.get("title", "")
    body = (item.get("body") or "")[:300]

    user_content = f"{source.upper()}: {title}"
    if host:
        user_content += f"\nHost: {host}"
    if location:
        user_content += f"\nStandort: {location}"
    if body:
        user_content += f"\nDetails: {body}"

    # HyDE step: enrich with RAG context if it-aikb is available
    if aikb_svc:
        rag_snippet = await _hyde_rag_lookup(item, llm, aikb_svc)
        if rag_snippet:
            user_content += f"\n\nRelevante Wissensbasis:\n{rag_snippet}"

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = await llm.ainvoke([
            SystemMessage(content=_EXPLAIN_SYSTEM_PROMPT),
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


async def enrich_single(item: dict, llm_config) -> str | None:
    """Enrich a single feed item on demand. Returns insight text or None."""
    if not llm_config or not llm_config.is_configured:
        return None
    try:
        llm = _build_llm(llm_config, timeout=60)
    except Exception as e:
        log.warning("Could not initialise LLM for on-demand enrichment: %s", e)
        return None
    aikb_svc = await _load_aikb_svc()
    return await _enrich_one(item, llm, aikb_svc)


async def _load_aikb_svc():
    """Load the it-aikb connector if configured."""
    try:
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.core.security import decrypt_credentials
        from app.models.connector import ConnectorConfig
        from app.services.connectors.it_aikb import ITAikbConnector
        async with AsyncSessionLocal() as s:
            result = await s.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.type == "it_aikb",
                    ConnectorConfig.enabled.is_(True),
                )
            )
            aikb_row = result.scalars().first()
            if aikb_row:
                creds = decrypt_credentials(aikb_row.encrypted_credentials)
                return ITAikbConnector(base_url=aikb_row.base_url, credentials=creds)
    except Exception as e:
        log.debug("Could not load it-aikb connector: %s", e)
    return None


async def enrich_batch(items: list[dict], llm_config) -> None:
    """Enrich a batch of feed items with AI insights (best-effort, non-blocking).

    Uses HyDE pattern when it-aikb connector is configured:
    LLM generates search query → it-aikb RAG lookup → context-aware explanation.
    """
    if not llm_config or not llm_config.is_configured:
        return

    targets = [i for i in items if i.get("severity") in _ENRICH_SEVERITIES]
    if not targets:
        return

    try:
        llm = _build_llm(llm_config)
    except Exception as e:
        log.warning("Could not initialise LLM for feed enrichment: %s", e)
        return

    # Load it-aikb connector for HyDE RAG context lookup
    aikb_svc = await _load_aikb_svc()

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _guarded(item: dict) -> None:
        async with sem:
            await _enrich_one(item, llm, aikb_svc)

    await asyncio.gather(*[_guarded(i) for i in targets], return_exceptions=True)
