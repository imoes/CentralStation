"""Worklist Builder — produces the AI-prioritised triage list for the bridge.

Runs on an interval (default 15 min). Instead of dumping thousands of raw alerts,
it bundles them into a ranked "what to tackle next" list:

  1. Aggregate open (non-resolved) alerts in OpenSearch, grouped by external_id
     → recurring alerts collapse into ONE entry with a count + age-of-oldest
  2. Score each group with the CPU scorer (severity, age, recurrence, criticality)
  3. Rank, take the top N
  4. Resolve a verdict per entry, in priority order:
       a) the existing ai_insight on the alert (already specific & German)
       b) a cached verdict from ai_insight_cache (recurring alert, reuse — no LLM)
       c) [optional] generate once via LLM and cache it
  5. Persist a WorklistSnapshot row the bridge reads instantly.

This means the slow LLM is never called at request time, and recurring alerts
never get re-analysed — exactly the caching the operator asked for.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _alert_state(critical: int, high: int) -> str:
    if critical > 0:
        return "red"
    if high > 0:
        return "yellow"
    return "green"


async def build_worklist(db: Any, *, hours: int = 24, size: int = 15) -> dict:
    """Build and persist the prioritised worklist. Returns the snapshot dict."""
    from app.core.opensearch import get_opensearch
    from app.services.alert_scorer import score_alert
    from app.services.settings import get_agent_config

    os_client = get_opensearch()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # ── 1. Group open alerts by external_id ──────────────────────────────────
    buckets = []
    try:
        resp = await os_client.search(
            index="cs-feed-*",
            body={
                "query": {
                    "bool": {
                        "must": [{"range": {"created_at": {"gte": since}}}],
                        "must_not": [{"term": {"status": "resolved"}}],
                    }
                },
                "size": 0,
                "aggs": {
                    "by_alert": {
                        "terms": {"field": "external_id", "size": 400},
                        "aggs": {
                            "latest": {"top_hits": {"size": 1, "sort": [{"created_at": {"order": "desc"}}]}},
                            "oldest": {"min": {"field": "created_at"}},
                        },
                    }
                },
            },
            ignore_unavailable=True,
        )
        buckets = resp.get("aggregations", {}).get("by_alert", {}).get("buckets", [])
    except Exception as e:
        log.warning("worklist: aggregation failed: %s", e)
        return await _save_snapshot(db, [], "green", 0)

    # ── 2. Build candidates + recent_counts (recurrence) ──────────────────────
    recent_counts: dict[str, int] = {}
    host_sources: dict[str, set] = {}
    candidates: list[dict] = []

    for b in buckets:
        ext_id = b["key"]
        count = b["doc_count"]
        hits = b.get("latest", {}).get("hits", {}).get("hits", [])
        if not hits:
            continue
        doc = hits[0]["_source"]
        meta = doc.get("metadata") or {}
        host = meta.get("host") or meta.get("agent") or meta.get("container_name") or ""
        recent_counts[ext_id] = count
        if host:
            host_sources.setdefault(host, set()).add(doc.get("source", ""))

        oldest_raw = b.get("oldest", {}).get("value_as_string") or b.get("oldest", {}).get("value")
        candidates.append({
            "external_id": ext_id,
            "count": count,
            "oldest": oldest_raw,
            "doc": doc,
            "host": host,
        })

    if not candidates:
        return await _save_snapshot(db, [], "green", 0)

    # ── 3. Adaptive adjustments (reuse scorer's learned deltas) ──────────────
    adjustments: dict[str, float] = {}
    try:
        from sqlalchemy import select
        from app.models.workflow import AlertScoreAdjustment
        from app.services.alert_scorer import _pattern_hash
        now = datetime.now(timezone.utc)
        ph_list = list({_pattern_hash(c["doc"]) for c in candidates})
        r = await db.execute(
            select(AlertScoreAdjustment).where(
                AlertScoreAdjustment.pattern_hash.in_(ph_list),
                (AlertScoreAdjustment.expires_at.is_(None)) | (AlertScoreAdjustment.expires_at > now),
            )
        )
        for row in r.scalars().all():
            adjustments[row.pattern_hash] = row.score_delta
    except Exception as e:
        log.debug("worklist: adjustments load failed: %s", e)

    # ── 4. Score & rank ───────────────────────────────────────────────────────
    agent_cfg = await get_agent_config(db)
    min_age = agent_cfg.interval_minutes
    flap_thr = agent_cfg.flap_threshold

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        score = score_alert(
            c["doc"], recent_counts, host_sources, adjustments,
            min_age_minutes=min_age, flap_threshold=flap_thr,
        )
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    top = scored[:size]

    # ── 5. Resolve verdicts (existing ai_insight → cache → generate) ─────────
    items = []
    for rank, (score, c) in enumerate(top, start=1):
        doc = c["doc"]
        meta = doc.get("metadata") or {}
        ext_id = c["external_id"]
        severity = doc.get("severity", "info")
        title = doc.get("title", "")
        verdict = (doc.get("ai_insight") or "").strip()

        # b) fall back to cache if no live insight
        if not verdict:
            verdict = await _get_cached_verdict(db, ext_id)

        # c) refresh / store cache when we DO have a live insight
        if doc.get("ai_insight"):
            await _store_cached_verdict(db, ext_id, severity, title, doc["ai_insight"],
                                        agent_cfg.score_delta_decay_days)

        items.append({
            "rank": rank,
            "external_id": ext_id,
            "severity": severity,
            "source": doc.get("source", ""),
            "title": title,
            "host": c["host"],
            "location": doc.get("location_name") or "",
            "verdict": verdict,
            "count": c["count"],
            "oldest": c["oldest"],
            "score": round(score, 1),
        })

    # ── 6. Overall state ─────────────────────────────────────────────────────
    crit = sum(1 for _, c in scored if c["doc"].get("severity") == "critical")
    high = sum(1 for _, c in scored if c["doc"].get("severity") == "high")
    state = _alert_state(crit, high)

    log.info("worklist: built %d items from %d open problems (state=%s)", len(items), len(candidates), state)
    return await _save_snapshot(db, items, state, len(candidates))


async def _get_cached_verdict(db: Any, cache_key: str) -> str:
    from sqlalchemy import select
    from app.models.workflow import AiInsightCache
    try:
        r = await db.execute(select(AiInsightCache).where(AiInsightCache.cache_key == cache_key))
        row = r.scalar_one_or_none()
        if row and row.verdict:
            row.hit_count += 1
            await db.commit()
            return row.verdict
    except Exception as e:
        log.debug("worklist: cache get failed for %s: %s", cache_key, e)
    return ""


async def _store_cached_verdict(db: Any, cache_key: str, severity: str, title: str,
                                verdict: str, decay_days: int) -> None:
    from sqlalchemy import select
    from app.models.workflow import AiInsightCache
    try:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=decay_days)
        r = await db.execute(select(AiInsightCache).where(AiInsightCache.cache_key == cache_key))
        row = r.scalar_one_or_none()
        if row:
            row.verdict = verdict
            row.severity = severity
            row.sample_title = title[:300]
            row.updated_at = now
            row.expires_at = expires
        else:
            db.add(AiInsightCache(
                cache_key=cache_key, severity=severity, sample_title=title[:300],
                verdict=verdict, hit_count=1, expires_at=expires,
            ))
        await db.commit()
    except Exception as e:
        log.debug("worklist: cache store failed for %s: %s", cache_key, e)


async def _save_snapshot(db: Any, items: list, state: str, open_count: int) -> dict:
    from app.models.workflow import WorklistSnapshot
    from sqlalchemy import delete, select
    snapshot = {
        "items": items,
        "alert_state": state,
        "open_count": open_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # keep only the latest few snapshots
        row = WorklistSnapshot(
            id=uuid.uuid4(), items=items, alert_state=state, open_count=open_count,
        )
        db.add(row)
        await db.commit()
        # prune older than the latest 5
        old = await db.execute(
            select(WorklistSnapshot).order_by(WorklistSnapshot.created_at.desc()).offset(5)
        )
        for o in old.scalars().all():
            await db.delete(o)
        await db.commit()
    except Exception as e:
        log.warning("worklist: snapshot save failed: %s", e)
    return snapshot


async def get_latest_worklist(db: Any) -> dict | None:
    """Return the most recent worklist snapshot, or None if none built yet."""
    from app.models.workflow import WorklistSnapshot
    from sqlalchemy import select
    try:
        r = await db.execute(
            select(WorklistSnapshot).order_by(WorklistSnapshot.created_at.desc()).limit(1)
        )
        row = r.scalar_one_or_none()
        if not row:
            return None
        return {
            "items": row.items or [],
            "alert_state": row.alert_state,
            "open_count": row.open_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    except Exception as e:
        log.debug("worklist: get latest failed: %s", e)
        return None
