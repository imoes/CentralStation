"""Generative UI Layout Engine — scores widgets by current operational relevance.

Algorithm (fully deterministic, no LLM):
  1. Read the latest AiAnalysis: severity_summary + findings (sources, severities, hosts)
  2. Read live severity counts from OpenSearch (cs-feed-*)
  3. Score each non-pinned widget 0–100 based on how relevant it is to the situation
  4. Sort by score desc, pack into a two-column grid (hot = large/top, cold = small/bottom)
  5. Pinned widgets stay exactly where they are

Score contributions:
  - Widget source matches finding source (+30)
  - Widget severity matches overall severity_summary (+25)
  - Widget type is ai_summary or top_hosts during incident (+20)
  - Absolute count in OpenSearch (normalised, +0–25)

NASA override rule: pinned=True means KI never moves that widget.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Grid parameters
CELL_H = 80          # px per row unit (matches GridStack cellHeight)
COLS = 12            # total grid columns

# Default sizes per widget type (w, h) in grid units
_DEFAULT_SIZE: dict[str, tuple[int, int]] = {
    "stat":         (2, 2),
    "donut":        (5, 5),
    "list":         (5, 4),
    "bar":          (5, 4),
    "top_hosts":    (4, 4),
    "ai_summary":   (5, 4),
    "timeseries":   (6, 4),
    "forecast":     (6, 4),
    "grafana_panel":(6, 5),
}

# Hot sizes — bigger when incident is relevant
_HOT_SIZE: dict[str, tuple[int, int]] = {
    "stat":         (3, 3),
    "donut":        (6, 5),
    "list":         (7, 5),
    "bar":          (6, 4),
    "top_hosts":    (5, 5),
    "ai_summary":   (7, 5),
    "timeseries":   (7, 4),
    "forecast":     (7, 4),
    "grafana_panel":(7, 5),
}

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "none": 0}


async def propose_layout(db: Any, dashboard_id: str, user_id: str) -> list[dict]:
    """Compute a new layout for all non-pinned widgets on a dashboard.

    Returns a list of placement dicts:
      {"widget_id": str, "gs_x": int, "gs_y": int, "gs_w": int, "gs_h": int, "hidden": bool}

    Pinned widgets are returned with their current position unchanged.
    """
    from sqlalchemy import select
    from app.models.workflow import DashboardWidget
    from app.models.ai import AiAnalysis

    # ── Load widgets ─────────────────────────────────────────────────────────
    r = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.user_id == user_id,
        )
    )
    widgets = r.scalars().all()
    if not widgets:
        return []

    # ── Load latest analysis ─────────────────────────────────────────────────
    r2 = await db.execute(
        select(AiAnalysis)
        .where(AiAnalysis.agent_type == "sysadmin")
        .order_by(AiAnalysis.run_at.desc())
        .limit(1)
    )
    analysis = r2.scalar_one_or_none()
    severity_summary = (analysis.severity_summary if analysis else "none") or "none"
    findings = analysis.findings or [] if analysis else []
    hot_sources = {f.get("source", "") for f in findings if f.get("severity") in ("critical", "high")}
    hot_severity = severity_summary

    # ── Live counts from OpenSearch ──────────────────────────────────────────
    sev_counts = await _get_severity_counts()
    max_count = max(sev_counts.values(), default=1) or 1

    # ── Score each widget ────────────────────────────────────────────────────
    scored: list[tuple[float, DashboardWidget]] = []
    for w in widgets:
        if w.pinned:
            continue
        score = _score_widget(w, hot_sources, hot_severity, sev_counts, max_count)
        scored.append((score, w))

    scored.sort(key=lambda x: -x[0])  # highest score first

    # ── Pack into grid ───────────────────────────────────────────────────────
    placements: list[dict] = []

    # Pinned widgets first — keep their position
    for w in widgets:
        if w.pinned:
            placements.append({
                "widget_id": str(w.id),
                "gs_x": w.gs_x, "gs_y": w.gs_y, "gs_w": w.gs_w, "gs_h": w.gs_h,
                "hidden": False,
                "pinned": True,
            })

    # Non-pinned: place in order of score, top-to-bottom, two columns
    incident_mode = SEVERITY_RANK.get(hot_severity, 0) >= 3  # high or critical

    current_x = 0
    current_y = _max_pinned_y(placements) + 1 if placements else 0
    col_heights = [current_y, current_y]  # left col (0) and right col (half)

    for score, w in scored:
        hot = score >= 60
        if incident_mode and hot:
            w_size, h_size = _HOT_SIZE.get(w.widget_type, (4, 3))
        else:
            w_size, h_size = _DEFAULT_SIZE.get(w.widget_type, (4, 3))

        # Hide very low-scoring widgets during a hot incident
        hidden = incident_mode and score < 15

        # Choose column: left (0) or right (COLS//2)
        left_h = col_heights[0]
        right_h = col_heights[1]
        half = COLS // 2
        if w_size > half:
            # Wide widget: full-width, below both columns
            y = max(left_h, right_h)
            x = 0
            w_size = COLS
            col_heights[0] = y + h_size
            col_heights[1] = y + h_size
        elif left_h <= right_h:
            x = 0
            y = left_h
            col_heights[0] = y + h_size
        else:
            x = half
            y = right_h
            col_heights[1] = y + h_size

        placements.append({
            "widget_id": str(w.id),
            "gs_x": x, "gs_y": y, "gs_w": w_size, "gs_h": h_size,
            "hidden": hidden,
            "pinned": False,
        })

    return placements


def _score_widget(w: Any, hot_sources: set[str], hot_severity: str,
                  sev_counts: dict[str, int], max_count: int) -> float:
    score = 0.0
    cfg = w.config or {}
    wtype = w.widget_type

    # ai_summary is always relevant — shows the current situation
    if wtype == "ai_summary":
        score += 30
        if SEVERITY_RANK.get(hot_severity, 0) >= 2:
            score += 20

    # top_hosts is relevant during incidents
    if wtype == "top_hosts":
        score += 10
        if SEVERITY_RANK.get(hot_severity, 0) >= 2:
            score += 15

    # Stat widget: score by matching severity
    if wtype == "stat":
        widget_sev = cfg.get("severity", "")
        if widget_sev == hot_severity:
            score += 35
        elif widget_sev in ("critical", "high") and SEVERITY_RANK.get(hot_severity, 0) >= 2:
            score += 20
        # boost by absolute count
        count = sev_counts.get(widget_sev, 0)
        score += 25 * count / max_count

    # List/bar/donut: score by source match
    if wtype in ("list", "bar", "donut"):
        sources = set(cfg.get("sources") or [])
        overlap = sources & hot_sources
        if overlap:
            score += 30
        # generic count boost
        total = sum(sev_counts.values())
        score += 10 * min(total, max_count) / max_count

    # Timeseries/forecast: useful during CPU/memory incidents
    if wtype in ("timeseries", "forecast"):
        if hot_severity in ("critical", "high"):
            score += 20
        else:
            score += 5

    # Grafana panel: medium relevance
    if wtype == "grafana_panel":
        score += 10

    return score


async def _get_severity_counts() -> dict[str, int]:
    """Return {severity: count} from the last hour across all cs-feed-* indices."""
    from app.core.opensearch import get_opensearch
    from datetime import datetime, timezone, timedelta
    try:
        os_client = get_opensearch()
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = await os_client.search(
            index="cs-feed-*",
            body={
                "query": {"range": {"created_at": {"gte": since}}},
                "aggs": {"by_sev": {"terms": {"field": "severity", "size": 10}}},
                "size": 0,
            },
            ignore_unavailable=True,
        )
        return {
            b["key"]: b["doc_count"]
            for b in resp.get("aggregations", {}).get("by_sev", {}).get("buckets", [])
        }
    except Exception as e:
        log.debug("layout_engine: severity counts failed: %s", e)
        return {}


def _max_pinned_y(placements: list[dict]) -> int:
    if not placements:
        return -1
    return max(p["gs_y"] + p["gs_h"] for p in placements) - 1
