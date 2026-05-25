"""Dashboard Widgets — per-user configurable GridStack widgets."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.workflow import DashboardWidget
from app.services import feed_index

router = APIRouter(prefix="/dashboard-widgets", tags=["dashboard-widgets"])

_DEFAULT_WIDGETS = [
    {
        "widget_type": "donut",
        "title": "Severity-Verteilung",
        "gs_x": 0, "gs_y": 0, "gs_w": 5, "gs_h": 5,
        "config": {"index_pattern": "cs-feed-*", "query_string": ""},
    },
    {
        "widget_type": "stat",
        "title": "Kritisch",
        "gs_x": 5, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "severity:critical"},
    },
    {
        "widget_type": "stat",
        "title": "Hoch",
        "gs_x": 7, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "severity:high"},
    },
    {
        "widget_type": "list",
        "title": "Neueste Alerts",
        "gs_x": 5, "gs_y": 2, "gs_w": 4, "gs_h": 3,
        "config": {"index_pattern": "cs-feed-*", "query_string": "", "limit": 8},
    },
]


class WidgetCreate(BaseModel):
    widget_type: str
    title: str
    gs_x: int = 0
    gs_y: int = 0
    gs_w: int = 4
    gs_h: int = 3
    config: dict = {}


class WidgetUpdate(BaseModel):
    title: str | None = None
    gs_x: int | None = None
    gs_y: int | None = None
    gs_w: int | None = None
    gs_h: int | None = None
    config: dict | None = None


def _to_dict(w: DashboardWidget) -> dict:
    return {
        "id": str(w.id),
        "user_id": str(w.user_id),
        "widget_type": w.widget_type,
        "title": w.title,
        "gs_x": w.gs_x, "gs_y": w.gs_y,
        "gs_w": w.gs_w, "gs_h": w.gs_h,
        "config": w.config or {},
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


async def _create_defaults(user_id: uuid.UUID, db: AsyncSession) -> list[DashboardWidget]:
    widgets = []
    for d in _DEFAULT_WIDGETS:
        w = DashboardWidget(id=uuid.uuid4(), user_id=user_id, **d)
        db.add(w)
        widgets.append(w)
    await db.commit()
    for w in widgets:
        await db.refresh(w)
    return widgets


@router.get("/")
async def list_widgets(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(DashboardWidget)
        .where(DashboardWidget.user_id == current_user.id)
        .order_by(DashboardWidget.gs_y, DashboardWidget.gs_x)
    )
    widgets = result.scalars().all()
    if not widgets:
        widgets = await _create_defaults(current_user.id, db)
    return [_to_dict(w) for w in widgets]


@router.post("/", status_code=201)
async def create_widget(
    body: WidgetCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    w = DashboardWidget(id=uuid.uuid4(), user_id=current_user.id, **body.model_dump())
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return _to_dict(w)


@router.patch("/{widget_id}")
async def update_widget(
    widget_id: uuid.UUID,
    body: WidgetUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Widget not found")
    if w.user_id != current_user.id:
        raise HTTPException(403, "Not your widget")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(w, field, val)
    await db.commit()
    await db.refresh(w)
    return _to_dict(w)


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Widget not found")
    if w.user_id != current_user.id:
        raise HTTPException(403, "Not your widget")
    await db.delete(w)
    await db.commit()


@router.get("/{widget_id}/data")
async def get_widget_data(
    widget_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Fetch data for a widget from OpenSearch or Prometheus."""
    result = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "Widget not found")
    if w.user_id != current_user.id:
        raise HTTPException(403, "Not your widget")

    cfg = w.config or {}
    index_pattern = cfg.get("index_pattern", "cs-feed-*")
    query_string = cfg.get("query_string", "")
    user_id_str = str(current_user.id)

    if w.widget_type == "stat":
        os_client = feed_index.get_opensearch()
        try:
            resp = await os_client.count(
                index=index_pattern,
                body={"query": {"query_string": {"query": query_string or "*"}} if query_string else {"match_all": {}}},
                ignore_unavailable=True,
            )
            return {"count": resp.get("count", 0)}
        except Exception:
            return {"count": 0}

    elif w.widget_type == "list":
        limit = int(cfg.get("limit", 8))
        items = await feed_index.search_by_query(
            index_pattern=index_pattern,
            query_string=query_string,
            size=limit,
            user_id=user_id_str,
        )
        return {"items": items}

    elif w.widget_type == "donut":
        os_client = feed_index.get_opensearch()
        body = {
            "size": 0,
            "query": {"query_string": {"query": query_string or "*"}} if query_string else {"match_all": {}},
            "aggs": {"by_severity": {"terms": {"field": "severity", "size": 10}}},
        }
        try:
            resp = await os_client.search(index=index_pattern, body=body, ignore_unavailable=True)
            buckets = [
                {"key": b["key"], "count": b["doc_count"]}
                for b in resp.get("aggregations", {}).get("by_severity", {}).get("buckets", [])
            ]
            return {"buckets": buckets}
        except Exception:
            return {"buckets": []}

    elif w.widget_type == "timeseries":
        # Prometheus-backed timeseries: delegate to PrometheusConnector
        from app.services.connectors import get_connector
        from app.core.security import decrypt_credentials
        from app.models.connector import ConnectorConfig as ConnectorModel
        from sqlalchemy import select as sa_select

        prom_result = await db.execute(
            sa_select(ConnectorModel)
            .where(ConnectorModel.type == "prometheus")
            .where(ConnectorModel.enabled == True)  # noqa: E712
            .limit(1)
        )
        prom_conn = prom_result.scalar_one_or_none()
        if not prom_conn:
            return {"series": [], "unit": "", "error": "No Prometheus connector configured"}

        credentials = decrypt_credentials(prom_conn.encrypted_credentials)
        connector = get_connector("prometheus", prom_conn.base_url, credentials)
        promql = cfg.get("promql", "")
        if not promql:
            return {"series": [], "unit": ""}
        hours = int(cfg.get("hours", 4))
        step = cfg.get("step", "1m")
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        try:
            result = await connector.query_range(promql, start.isoformat(), end.isoformat(), step)
            data_results = result.get("data", {}).get("result", [])
            if not data_results:
                return {"series": [], "unit": cfg.get("unit", "")}
            series = [
                {"time": datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(), "value": float(v)}
                for ts, v in data_results[0].get("values", [])
            ]
            return {"series": series, "unit": cfg.get("unit", "")}
        except Exception as e:
            return {"series": [], "unit": "", "error": str(e)}

    elif w.widget_type == "grafana_panel":
        # Grafana panel is rendered client-side as iframe; backend returns URL only
        return {"panel_url": cfg.get("panel_url", ""), "refresh_seconds": cfg.get("refresh_seconds", 30)}

    return {}
