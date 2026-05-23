"""Feed Enricher — adds a short AI explanation to new feed alerts.

Called from alert_aggregator after indexing new alerts.
Generates a 2-3 sentence German explanation + first action step.
Results are stored back to OpenSearch as the `ai_insight` field.

Only enriches critical/high/warning severity to keep token usage reasonable.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_ENRICH_SEVERITIES = {"critical", "high", "warning"}
_MAX_CONCURRENT = 5  # parallel LLM calls

_SYSTEM_PROMPT = (
    "Du bist ein erfahrener Linux-Sysadmin. "
    "Erkläre die folgende Monitoring-Meldung in 2-3 Sätzen: Was bedeutet sie, "
    "was ist die wahrscheinliche Ursache, und was ist die erste konkrete Maßnahme? "
    "Antworte auf Deutsch. Keine Markdown-Formatierung. Maximal 400 Zeichen."
)


async def _enrich_one(item: dict, llm) -> None:
    """Generate and store an AI insight for a single feed item."""
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

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])
        insight = (response.content or "").strip()[:500]
        if not insight:
            return

        doc_id = item.get("id") or item.get("external_id")
        if not doc_id:
            return

        os_client = get_opensearch()
        await os_client.update(
            index=_index(source),
            id=str(doc_id),
            body={"doc": {"ai_insight": insight}},
        )
    except Exception as e:
        log.debug("Feed enrichment failed for %s: %s", item.get("id"), e)


async def enrich_batch(items: list[dict], llm_config) -> None:
    """Enrich a batch of feed items with AI insights (best-effort, non-blocking)."""
    if not llm_config or not llm_config.is_configured:
        return

    targets = [i for i in items if i.get("severity") in _ENRICH_SEVERITIES]
    if not targets:
        return

    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            base_url=llm_config.base_url,
            model=llm_config.model,
            api_key=llm_config.api_key or "none",
            max_tokens=200,
            timeout=30,
        )
    except Exception as e:
        log.warning("Could not initialise LLM for feed enrichment: %s", e)
        return

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _guarded(item: dict) -> None:
        async with sem:
            await _enrich_one(item, llm)

    await asyncio.gather(*[_guarded(i) for i in targets], return_exceptions=True)
