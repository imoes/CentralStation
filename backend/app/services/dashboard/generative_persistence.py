"""Shared persistence for scheduled and user-triggered dashboard composition."""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.workflow import Dashboard, DashboardWidget
from app.services.dashboard.generative_designer import GENERATIVE_DASHBOARD_NAME, semantic_widget_key


_generation_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def generation_lock(user_id: str) -> asyncio.Lock:
    """Serialize dashboard generation per user within this backend process."""
    return _generation_locks[user_id]


def _generation_is_obsolete(current_meta: dict | None, spec_meta: dict | None) -> bool:
    """Reject a composition whose input snapshot predates source invalidation."""
    current = current_meta or {}
    if current.get("result_state") != "refresh_pending":
        return False
    invalidated_at = str(current.get("invalidated_at") or "")
    generated_from = str((spec_meta or {}).get("as_of") or "")
    return bool(invalidated_at) and (not generated_from or generated_from <= invalidated_at)


async def invalidate_generated_rationales(
    db,
    *,
    reason: str,
    affected_hosts: set[str] | None = None,
) -> int:
    """Remove persisted briefing claims after their source state changes.

    Widget data is fetched live, but the rationale is generated prose.  Clear
    that prose synchronously so a recovered host cannot remain visible while a
    slower LLM refresh is still pending.
    """
    dashboards = (await db.execute(
        select(Dashboard).where(Dashboard.name == GENERATIVE_DASHBOARD_NAME)
    )).scalars().all()
    if affected_hosts:
        needles = {host.lower() for host in affected_hosts if host}
        dashboards = [
            dashboard for dashboard in dashboards
            if any(host in (dashboard.rationale or "").lower() for host in needles)
        ]
    if not dashboards:
        return 0

    invalidated_at = datetime.now(timezone.utc)
    for dashboard in dashboards:
        meta = dict(dashboard.generation_meta or {})
        meta.update({
            "result_state": "refresh_pending",
            "stale_reason": reason,
            "as_of": invalidated_at.isoformat(),
            "invalidated_at": invalidated_at.isoformat(),
        })
        dashboard.rationale = ""
        dashboard.generation_meta = meta
    await db.commit()

    from app.api.ws import manager
    for dashboard in dashboards:
        await manager.send_user(str(dashboard.user_id), {
            "type": "dashboard_updated",
            "dashboard_id": str(dashboard.id),
            "generated_at": dashboard.generated_at.isoformat() if dashboard.generated_at else None,
        })
    return len(dashboards)


async def apply_generated_dashboard(db, dashboard: Dashboard, spec: dict) -> None:
    """Reconcile a generated spec while retaining pins and stable widget IDs."""
    # The in-process lock avoids duplicate LLM runs in one worker; this row lock
    # also serializes scheduler and request workers that share the database.
    await db.execute(
        select(Dashboard).where(Dashboard.id == dashboard.id).with_for_update()
    )
    if _generation_is_obsolete(dashboard.generation_meta, spec.get("meta")):
        return
    rows = (await db.execute(
        select(DashboardWidget).where(DashboardWidget.dashboard_id == dashboard.id)
    )).scalars().all()
    pinned = [w for w in rows if w.pinned]
    reusable: dict[tuple, list[DashboardWidget]] = {}
    for widget in rows:
        if not widget.pinned:
            reusable.setdefault(semantic_widget_key({
                "widget_type": widget.widget_type,
                "config": widget.config or {},
            }), []).append(widget)

    pinned_keys = {
        semantic_widget_key({"widget_type": w.widget_type, "config": w.config or {}})
        for w in pinned
    }
    favorite_rows = max((w.gs_y + w.gs_h for w in pinned), default=0)
    retained_ids: set[uuid.UUID] = {w.id for w in pinned}

    for widget_spec in spec.get("widgets", []):
        key = semantic_widget_key(widget_spec)
        if key in pinned_keys:
            continue
        candidates = reusable.get(key) or []
        widget = candidates.pop(0) if candidates else DashboardWidget(
            id=uuid.uuid4(), user_id=dashboard.user_id, dashboard_id=dashboard.id,
        )
        widget.widget_type = widget_spec["widget_type"]
        widget.title = widget_spec["title"]
        widget.gs_x = widget_spec["gs_x"]
        widget.gs_y = widget_spec["gs_y"] + favorite_rows
        widget.gs_w = widget_spec["gs_w"]
        widget.gs_h = widget_spec["gs_h"]
        widget.config = widget_spec["config"]
        widget.hidden = False
        if widget not in rows:
            db.add(widget)
        retained_ids.add(widget.id)

    for widget in rows:
        if widget.id not in retained_ids:
            await db.delete(widget)

    dashboard.rationale = spec.get("rationale") or ""
    dashboard.generation_meta = spec.get("meta") or {}
    dashboard.generated_at = datetime.now(timezone.utc)
    await db.flush()
