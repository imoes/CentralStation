"""Shared persistence for scheduled and user-triggered dashboard composition."""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.workflow import Dashboard, DashboardWidget
from app.services.dashboard.generative_designer import semantic_widget_key


_generation_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def generation_lock(user_id: str) -> asyncio.Lock:
    """Serialize dashboard generation per user within this backend process."""
    return _generation_locks[user_id]


async def apply_generated_dashboard(db, dashboard: Dashboard, spec: dict) -> None:
    """Reconcile a generated spec while retaining pins and stable widget IDs."""
    # The in-process lock avoids duplicate LLM runs in one worker; this row lock
    # also serializes scheduler and request workers that share the database.
    await db.execute(
        select(Dashboard).where(Dashboard.id == dashboard.id).with_for_update()
    )
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
