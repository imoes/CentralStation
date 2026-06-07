"""Past-Incident-Memory for the AI agent and diagnostics.

When a new alert arrives or a diagnosis is requested, this module searches
historical data for similar past incidents so the LLM has context:
  "This pattern last occurred on 2026-05-12, resolved by restarting DCX API."

Data sources (no vector DB needed — plain SQL + JSONB):
  1. ai_analyses table  — past findings with matching host/severity
  2. workflow_sessions  — ITIL work sessions with root_cause / resolution_summary
  3. alert_score_adjustments — recurring pattern hashes (high sample_count)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 30
_MAX_RESULTS = 3


async def find_similar_incidents(
    host: str,
    db: Any,
    *,
    alert_title: str | None = None,
    lookback_days: int = _LOOKBACK_DAYS,
    limit: int = _MAX_RESULTS,
) -> list[dict]:
    """Search past incidents relevant to this host.

    Returns a list of dicts:
        [{run_at, severity, finding_title, recommendation, resolution, source}]

    source = "ai_analyses" | "workflow_session" | "recurring_pattern"
    """
    from sqlalchemy import text
    results: list[dict] = []
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # ── 1. ai_analyses: findings for this host ────────────────────────────────
    try:
        rows = await db.execute(
            text("""
                SELECT id, run_at, severity_summary, findings, recommendations
                FROM ai_analyses
                WHERE run_at >= :since
                  AND findings::text ILIKE :host_pat
                ORDER BY run_at DESC
                LIMIT 10
            """),
            {"since": since, "host_pat": f"%{host}%"},
        )
        for row in rows:
            findings = row.findings or []
            if not isinstance(findings, list):
                import json as _json
                try:
                    findings = _json.loads(findings)
                except Exception:
                    findings = []
            host_findings = [
                f for f in findings
                if host.lower() in (f.get("host") or "").lower()
                # skip meta-findings that just flag missing raw data — they're noise
                and not (f.get("title") or "").startswith("UNGEKLÄRT:")
            ]
            if not host_findings:
                continue  # analysis contains no finding for this host — skip
            for f in host_findings[:1]:
                recs = row.recommendations or []
                if not isinstance(recs, list):
                    import json as _json
                    try:
                        recs = _json.loads(recs)
                    except Exception:
                        recs = []
                # Prefer a recommendation that mentions this host; fall back to first
                host_rec = next(
                    (r for r in recs if host.lower() in (r.get("action") or "").lower()),
                    recs[0] if recs else None,
                )
                rec_text = host_rec.get("action", "") if host_rec else ""
                results.append({
                    "source": "ai_analyses",
                    "run_at": row.run_at.isoformat() if row.run_at else "",
                    "severity": row.severity_summary or "?",
                    "finding_title": f.get("title", ""),
                    "recommendation": rec_text[:200],
                    "resolution": "",
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
    except Exception as e:
        log.debug("past_incidents ai_analyses query failed: %s", e)

    # ── 2. workflow_sessions: ITIL sessions for this host ─────────────────────
    if len(results) < limit:
        try:
            rows = await db.execute(
                text("""
                    SELECT ws.created_at, ws.root_cause, ws.resolution_summary,
                           ws.jira_key, ws.ai_suggested_solution
                    FROM workflow_sessions ws
                    WHERE ws.created_at >= :since
                      AND (
                        ws.root_cause ILIKE :host_pat
                        OR ws.resolution_summary ILIKE :host_pat
                        OR ws.jira_key IS NOT NULL
                      )
                    ORDER BY ws.created_at DESC
                    LIMIT 5
                """),
                {"since": since, "host_pat": f"%{host}%"},
            )
            for row in rows:
                if len(results) >= limit:
                    break
                if not row.root_cause and not row.resolution_summary:
                    continue
                results.append({
                    "source": "workflow_session",
                    "run_at": row.created_at.isoformat() if row.created_at else "",
                    "severity": "?",
                    "finding_title": f"Jira: {row.jira_key or 'unbekannt'}",
                    "recommendation": (row.root_cause or "")[:200],
                    "resolution": (row.resolution_summary or "")[:200],
                })
        except Exception as e:
            log.debug("past_incidents workflow_sessions query failed: %s", e)

    # ── 3. alert_score_adjustments: recurring patterns for this host ──────────
    if len(results) < limit:
        try:
            rows = await db.execute(
                text("""
                    SELECT pattern_hash, pattern_desc, score_delta, sample_count, updated_at
                    FROM alert_score_adjustments
                    WHERE pattern_desc ILIKE :host_pat
                      AND sample_count >= 3
                    ORDER BY sample_count DESC
                    LIMIT 3
                """),
                {"host_pat": f"%{host}%"},
            )
            for row in rows:
                if len(results) >= limit:
                    break
                results.append({
                    "source": "recurring_pattern",
                    "run_at": row.updated_at.isoformat() if row.updated_at else "",
                    "severity": "?",
                    "finding_title": row.pattern_desc or row.pattern_hash,
                    "recommendation": f"Bekanntes Muster ({row.sample_count}x aufgetreten)",
                    "resolution": "",
                })
        except Exception as e:
            log.debug("past_incidents score_adjustments query failed: %s", e)

    # ── 4. OpenSearch: AI-resolved alerts similar to this host/title ─────────
    if len(results) < limit:
        try:
            from app.services.feed_index import search_ai_resolved
            os_results = await search_ai_resolved(
                alert_title=alert_title,
                host=host,
                limit=limit - len(results),
            )
            results.extend(os_results)
        except Exception as e:
            log.debug("past_incidents ai_resolved search failed: %s", e)

    return results[:limit]


def format_past_incidents_for_llm(incidents: list[dict]) -> str:
    """Format past incidents as a compact LLM context block."""
    if not incidents:
        return ""
    lines = ["Similar past incidents (last 30 days):"]
    for i, inc in enumerate(incidents, 1):
        ts = inc.get("run_at", "")[:10]
        sev = inc.get("severity", "?")
        title = inc.get("finding_title", "?")
        rec = inc.get("recommendation", "")
        res = inc.get("resolution", "")
        src = inc.get("source", "?")
        line = f"  {i}. [{ts}][{sev}] {title} (source: {src})"
        if rec:
            line += f"\n     Recommendation: {rec[:100]}"
        if res:
            line += f"\n     Resolution: {res[:100]}"
        lines.append(line)
    return "\n".join(lines)
