"""CPU-based alert scoring — no LLM, no GPU.

Two-layer scoring:
  1. Deterministic base score: severity, age, flapping, topology, status
  2. Adaptive delta: loaded from alert_score_adjustments (pattern-level learned corrections)

Usage:
    scored = await score_alerts_batch(alerts, db, min_age_minutes=10)
    top = [a for score, a in scored if score >= threshold]
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

# Base scores per severity
_SEVERITY_SCORE: dict[str, float] = {
    "critical": 100.0,
    "high":     70.0,
    "medium":   35.0,
    "low":      10.0,
    "info":     5.0,
    "warning":  50.0,  # legacy alias
}


def _pattern_hash(alert: dict) -> str:
    """Stable 12-char hash identifying an alert pattern (source + title prefix)."""
    source = alert.get("source", "")
    title = (alert.get("title") or "")[:60]
    raw = f"{source}:{title}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def score_alert(
    alert: dict,
    recent_counts: dict[str, int],
    host_sources: dict[str, set],
    adjustments: dict[str, float],
    min_age_minutes: int = 10,
    flap_threshold: int = 3,
) -> float:
    """Calculate a numeric relevance score for a single alert (CPU-only).

    Args:
        alert: dict with keys: severity, source, title, status, ai_insight,
               created_at (ISO str), external_id, metadata (dict)
        recent_counts: {external_id: count_in_flap_window}
        host_sources: {host: set_of_sources}  for cross-source bonus
        adjustments: {pattern_hash: score_delta}  adaptive learned corrections
        min_age_minutes: CheckMK stability threshold from settings
        flap_threshold: occurrences above which = flapping
    """
    score: float = 0.0

    # ── Severity ──────────────────────────────────────────────────────────────
    severity = (alert.get("severity") or "info").lower()
    score += _SEVERITY_SCORE.get(severity, 5.0)

    # ── Novelty: ai_insight presence ─────────────────────────────────────────
    if alert.get("ai_insight"):
        score -= 30.0   # already analysed — deprioritise unless something changed
    else:
        score += 40.0   # never analysed → high priority

    # ── Age scoring (uses min_age_minutes as calibration point) ──────────────
    created_raw = alert.get("created_at") or alert.get("timestamp")
    age_minutes = 0.0
    if created_raw:
        try:
            if isinstance(created_raw, str):
                from dateutil.parser import parse as _parse
                created_dt = _parse(created_raw)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
            else:
                created_dt = created_raw
            age_minutes = (datetime.now(timezone.utc) - created_dt).total_seconds() / 60
        except Exception:
            pass

    if age_minutes < min_age_minutes:
        score -= 40.0   # too young — may be transient (reboot, flap start)
    elif age_minutes > 180:
        score += 20.0   # open for >3h without resolution → escalate

    # ── Flapping detection ────────────────────────────────────────────────────
    ext_id = alert.get("external_id") or ""
    if ext_id and recent_counts.get(ext_id, 0) > flap_threshold:
        score -= 50.0   # same alert keeps firing → suppress LLM

    # ── Cross-source correlation bonus ────────────────────────────────────────
    host = (
        alert.get("host")
        or (alert.get("metadata") or {}).get("host")
        or (alert.get("metadata") or {}).get("agent")
        or ""
    )
    if host and len(host_sources.get(host, set())) >= 2:
        score += 25.0   # same host has alerts from multiple sources → correlated incident

    # ── Critical infrastructure bonus ────────────────────────────────────────
    meta = alert.get("metadata") or {}
    if meta.get("criticality") == "critical":
        score += 20.0

    # ── Status malus ─────────────────────────────────────────────────────────
    if alert.get("status") == "acknowledged":
        score -= 40.0

    # ── Adaptive delta (learned from feedback) ────────────────────────────────
    ph = _pattern_hash(alert)
    score += adjustments.get(ph, 0.0)

    return score


async def score_alerts_batch(
    alerts: list[dict],
    db: Any,
    min_age_minutes: int = 10,
    flap_window_minutes: int = 30,
    flap_threshold: int = 3,
) -> list[tuple[float, dict]]:
    """Score a batch of alerts and return sorted list (highest score first).

    Performs one OpenSearch aggregation to detect flapping, then scores all
    alerts in pure Python.  DB is used only to load adaptive adjustments.
    """
    if not alerts:
        return []

    # ── 1. Flapping counts from OpenSearch ───────────────────────────────────
    recent_counts: dict[str, int] = {}
    try:
        from app.core.opensearch import get_opensearch
        since = (datetime.now(timezone.utc) - timedelta(minutes=flap_window_minutes)).isoformat()
        ext_ids = [a.get("external_id") for a in alerts if a.get("external_id")]
        if ext_ids:
            os_client = get_opensearch()
            resp = await os_client.search(
                index="cs-feed-*",
                body={
                    "query": {
                        "bool": {
                            "must": [
                                {"terms": {"external_id": ext_ids}},
                                {"range": {"created_at": {"gte": since}}},
                            ]
                        }
                    },
                    "aggs": {"by_ext_id": {"terms": {"field": "external_id", "size": len(ext_ids)}}},
                    "size": 0,
                },
                ignore_unavailable=True,
            )
            for b in resp.get("aggregations", {}).get("by_ext_id", {}).get("buckets", []):
                recent_counts[b["key"]] = b["doc_count"]
    except Exception as e:
        log.debug("alert_scorer: flap count query failed: %s", e)

    # ── 2. Host→sources mapping (no I/O) ─────────────────────────────────────
    host_sources: dict[str, set] = {}
    for a in alerts:
        host = (
            a.get("host")
            or (a.get("metadata") or {}).get("host")
            or (a.get("metadata") or {}).get("agent")
            or ""
        )
        if host:
            host_sources.setdefault(host, set()).add(a.get("source", ""))

    # ── 3. Adaptive adjustments from DB ──────────────────────────────────────
    adjustments: dict[str, float] = {}
    if db is not None:
        try:
            from sqlalchemy import select
            from app.models.workflow import AlertScoreAdjustment
            ph_list = list({_pattern_hash(a) for a in alerts})
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(AlertScoreAdjustment).where(
                    AlertScoreAdjustment.pattern_hash.in_(ph_list),
                    (AlertScoreAdjustment.expires_at.is_(None))
                    | (AlertScoreAdjustment.expires_at > now),
                )
            )
            for row in result.scalars().all():
                adjustments[row.pattern_hash] = row.score_delta
        except Exception as e:
            log.debug("alert_scorer: adjustments load failed: %s", e)

    # ── 4. Score each alert ───────────────────────────────────────────────────
    scored = [
        (
            score_alert(a, recent_counts, host_sources, adjustments, min_age_minutes, flap_threshold),
            a,
        )
        for a in alerts
    ]
    scored.sort(key=lambda x: -x[0])
    return scored
