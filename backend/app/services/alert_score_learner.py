"""Adaptive score learning — updates alert_score_adjustments from feedback.

Feedback sources:
  - Jira ticket created for this alert type   → +20 (14d)
  - User manually clicked "KI Analyse"        → +15 (7d)
  - Alert open >4h, no ticket, no ack         → +10 (7d)  [called from housekeeping]
  - Alert acknowledged within 5 min           → −8  (3d)
  - Alert had ai_insight, no reaction 24h     → −5  (7d)  [called from housekeeping]

All deltas are clamped to [−80, +80] to prevent runaway learning.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

_MAX_DELTA = 80.0
_MIN_DELTA = -80.0


def _make_hash(alert: dict) -> tuple[str, str]:
    """Return (pattern_hash, pattern_desc) for an alert dict."""
    source = alert.get("source", "")
    title = (alert.get("title") or "")[:60]
    raw = f"{source}:{title}"
    ph = hashlib.md5(raw.encode()).hexdigest()[:12]
    return ph, raw


async def _apply_delta(
    db: Any,
    pattern_hash: str,
    pattern_desc: str,
    delta: float,
    decay_days: int,
) -> None:
    """Upsert a score_delta for the given pattern."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.workflow import AlertScoreAdjustment

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=decay_days)
    try:
        result = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(AlertScoreAdjustment)
            .where(AlertScoreAdjustment.pattern_hash == pattern_hash)
        )
        row = result.scalar_one_or_none()
        if row:
            new_delta = max(_MIN_DELTA, min(_MAX_DELTA, row.score_delta + delta))
            row.score_delta = new_delta
            row.sample_count += 1
            row.expires_at = max(row.expires_at or expires, expires)
            row.updated_at = now
        else:
            clamped = max(_MIN_DELTA, min(_MAX_DELTA, delta))
            db.add(AlertScoreAdjustment(
                pattern_hash=pattern_hash,
                pattern_desc=pattern_desc[:200],
                score_delta=clamped,
                sample_count=1,
                expires_at=expires,
                updated_at=now,
            ))
        await db.commit()
        log.debug("score_learner: %s delta %+.0f (total: %+.0f)", pattern_hash, delta, row.score_delta if row else clamped)
    except Exception as e:
        log.debug("score_learner: delta apply failed for %s: %s", pattern_hash, e)


async def record_jira_created(alert: dict, db: Any) -> None:
    """Alert led to a Jira ticket — boost this pattern."""
    ph, desc = _make_hash(alert)
    await _apply_delta(db, ph, desc, delta=+20.0, decay_days=14)


async def record_manual_enrich_requested(alert: dict, db: Any) -> None:
    """User clicked 'KI Analyse' on a skipped alert — it was relevant."""
    ph, desc = _make_hash(alert)
    await _apply_delta(db, ph, desc, delta=+15.0, decay_days=7)


async def record_alert_ignored(alert: dict, db: Any) -> None:
    """Alert was open >4h without ticket or ack — was important but ignored."""
    ph, desc = _make_hash(alert)
    await _apply_delta(db, ph, desc, delta=+10.0, decay_days=7)


async def record_quick_ack(alert: dict, db: Any) -> None:
    """Alert acknowledged within 5 min — probably routine noise."""
    ph, desc = _make_hash(alert)
    await _apply_delta(db, ph, desc, delta=-8.0, decay_days=3)


async def record_insight_ignored(alert: dict, db: Any) -> None:
    """Alert had ai_insight but no user reaction after 24h — analysis was wasted."""
    ph, desc = _make_hash(alert)
    await _apply_delta(db, ph, desc, delta=-5.0, decay_days=7)


async def cleanup_expired_adjustments(db: Any) -> int:
    """Reset expired score deltas to 0. Returns number of rows cleaned."""
    from sqlalchemy import select, update
    from app.models.workflow import AlertScoreAdjustment
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            select(AlertScoreAdjustment).where(
                AlertScoreAdjustment.expires_at.isnot(None),
                AlertScoreAdjustment.expires_at <= now,
                AlertScoreAdjustment.score_delta != 0,
            )
        )
        expired = result.scalars().all()
        for row in expired:
            row.score_delta = 0.0
            row.expires_at = None
        await db.commit()
        if expired:
            log.info("score_learner: reset %d expired adjustments", len(expired))
        return len(expired)
    except Exception as e:
        log.debug("score_learner: cleanup failed: %s", e)
        return 0
