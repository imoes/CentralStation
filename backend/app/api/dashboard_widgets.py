"""Dashboard Widgets — per-user configurable GridStack widgets."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db
from app.models.workflow import Dashboard, DashboardWidget
from app.services import feed_index

router = APIRouter(prefix="/dashboard-widgets", tags=["dashboard-widgets"])

_DEFAULT_WIDGETS = [
    {
        "widget_type": "stat",
        "title": "Kritisch",
        "gs_x": 0, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "severity:critical AND NOT status:resolved"},
    },
    {
        "widget_type": "stat",
        "title": "Hoch",
        "gs_x": 2, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "severity:high AND NOT status:resolved"},
    },
    {
        "widget_type": "stat",
        "title": "Mittel",
        "gs_x": 4, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "severity:medium AND NOT status:resolved"},
    },
    {
        "widget_type": "stat",
        "title": "Gesamt",
        "gs_x": 6, "gs_y": 0, "gs_w": 2, "gs_h": 2,
        "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved"},
    },
    {
        "widget_type": "ai_summary",
        "title": "KI-Lagebericht",
        "gs_x": 8, "gs_y": 0, "gs_w": 4, "gs_h": 2,
        "config": {"agent_type": "sysadmin"},
    },
    {
        "widget_type": "list",
        "title": "Aktive Alerts",
        "gs_x": 0, "gs_y": 2, "gs_w": 7, "gs_h": 5,
        "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved", "limit": 15},
    },
    {
        "widget_type": "top_hosts",
        "title": "Top Problem-Hosts",
        "gs_x": 7, "gs_y": 2, "gs_w": 5, "gs_h": 3,
        "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved", "limit": 8},
    },
    {
        "widget_type": "donut",
        "title": "Severity-Verteilung",
        "gs_x": 7, "gs_y": 5, "gs_w": 5, "gs_h": 3,
        "config": {"index_pattern": "cs-feed-*", "query_string": "NOT status:resolved"},
    },
]


class WidgetCreate(BaseModel):
    widget_type: str
    title: str
    dashboard_id: uuid.UUID | None = None
    gs_x: int = 0
    gs_y: int = 0
    gs_w: int = 4
    gs_h: int = 3
    config: dict = {}


class WidgetUpdate(BaseModel):
    title: str | None = None
    widget_type: str | None = None
    gs_x: int | None = None
    gs_y: int | None = None
    gs_w: int | None = None
    gs_h: int | None = None
    config: dict | None = None


class DashboardCreate(BaseModel):
    name: str
    description: str | None = None
    is_default: bool = False


class DashboardUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    position: int | None = None


def _dashboard_to_dict(d: Dashboard) -> dict:
    return {
        "id": str(d.id),
        "user_id": str(d.user_id),
        "name": d.name,
        "description": d.description,
        "is_default": d.is_default,
        "position": d.position,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _to_dict(w: DashboardWidget) -> dict:
    return {
        "id": str(w.id),
        "user_id": str(w.user_id),
        "dashboard_id": str(w.dashboard_id) if w.dashboard_id else None,
        "widget_type": w.widget_type,
        "title": w.title,
        "gs_x": w.gs_x, "gs_y": w.gs_y,
        "gs_w": w.gs_w, "gs_h": w.gs_h,
        "config": w.config or {},
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


async def _create_defaults(user_id: uuid.UUID, dashboard_id: uuid.UUID, db: AsyncSession) -> list[DashboardWidget]:
    widgets = []
    for d in _DEFAULT_WIDGETS:
        w = DashboardWidget(id=uuid.uuid4(), user_id=user_id, dashboard_id=dashboard_id, **d)
        db.add(w)
        widgets.append(w)
    return widgets


async def _ensure_default_dashboard(user_id: uuid.UUID, db: AsyncSession) -> Dashboard:
    result = await db.execute(
        select(Dashboard)
        .where(Dashboard.user_id == user_id)
        .order_by(Dashboard.is_default.desc(), Dashboard.position, Dashboard.created_at)
        .limit(1)
    )
    dashboard = result.scalar_one_or_none()
    if dashboard:
        return dashboard

    dashboard = Dashboard(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Operations Cockpit",
        description="Standard-Dashboard mit Ampel, KI-Lagebericht, aktiven Alerts und Top-Hosts.",
        is_default=True,
        position=0,
    )
    db.add(dashboard)
    await db.flush()

    orphan_result = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.user_id == user_id,
            DashboardWidget.dashboard_id == None,  # noqa: E711
        )
    )
    orphans = orphan_result.scalars().all()
    if orphans:
        for widget in orphans:
            widget.dashboard_id = dashboard.id
    else:
        await _create_defaults(user_id, dashboard.id, db)
    await db.commit()
    await db.refresh(dashboard)
    return dashboard


async def _get_dashboard_or_404(
    dashboard_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Dashboard:
    result = await db.execute(
        select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.user_id == user_id)
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(404, "Dashboard not found")
    return dashboard


@router.get("/dashboards")
async def list_dashboards(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _ensure_default_dashboard(current_user.id, db)
    result = await db.execute(
        select(Dashboard)
        .where(Dashboard.user_id == current_user.id)
        .order_by(Dashboard.is_default.desc(), Dashboard.position, Dashboard.created_at)
    )
    return [_dashboard_to_dict(d) for d in result.scalars().all()]


@router.post("/dashboards", status_code=201)
async def create_dashboard(
    body: DashboardCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.is_default:
        existing = await db.execute(select(Dashboard).where(Dashboard.user_id == current_user.id))
        for dashboard in existing.scalars().all():
            dashboard.is_default = False
    dashboard = Dashboard(
        id=uuid.uuid4(),
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        is_default=body.is_default,
    )
    db.add(dashboard)
    await db.commit()
    await db.refresh(dashboard)
    return _dashboard_to_dict(dashboard)


@router.patch("/dashboards/{dashboard_id}")
async def update_dashboard(
    dashboard_id: uuid.UUID,
    body: DashboardUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    dashboard = await _get_dashboard_or_404(dashboard_id, current_user.id, db)
    if body.is_default:
        existing = await db.execute(select(Dashboard).where(Dashboard.user_id == current_user.id))
        for row in existing.scalars().all():
            row.is_default = False
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(dashboard, field, value)
    await db.commit()
    await db.refresh(dashboard)
    return _dashboard_to_dict(dashboard)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    dashboard = await _get_dashboard_or_404(dashboard_id, current_user.id, db)
    result = await db.execute(select(Dashboard).where(Dashboard.user_id == current_user.id))
    dashboards = result.scalars().all()
    if len(dashboards) <= 1:
        raise HTTPException(400, "At least one dashboard is required")
    await db.delete(dashboard)
    await db.commit()


@router.post("/dashboards/{dashboard_id}/reset-defaults", status_code=200)
async def reset_dashboard_defaults(
    dashboard_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete all widgets for this dashboard and recreate the default set."""
    dashboard = await _get_dashboard_or_404(dashboard_id, current_user.id, db)
    existing = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.dashboard_id == dashboard.id,
            DashboardWidget.user_id == current_user.id,
        )
    )
    for w in existing.scalars().all():
        await db.delete(w)
    await db.flush()
    widgets = await _create_defaults(current_user.id, dashboard.id, db)
    await db.commit()
    for w in widgets:
        await db.refresh(w)
    return [_to_dict(w) for w in widgets]


@router.get("/")
async def list_widgets(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    dashboard_id: uuid.UUID | None = Query(None),
):
    dashboard = (
        await _get_dashboard_or_404(dashboard_id, current_user.id, db)
        if dashboard_id
        else await _ensure_default_dashboard(current_user.id, db)
    )
    result = await db.execute(
        select(DashboardWidget)
        .where(DashboardWidget.user_id == current_user.id, DashboardWidget.dashboard_id == dashboard.id)
        .order_by(DashboardWidget.gs_y, DashboardWidget.gs_x)
    )
    widgets = result.scalars().all()
    if not widgets:
        widgets = await _create_defaults(current_user.id, dashboard.id, db)
        await db.commit()
        for w in widgets:
            await db.refresh(w)
    return [_to_dict(w) for w in widgets]


@router.post("/", status_code=201)
async def create_widget(
    body: WidgetCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    body_data = body.model_dump()
    dashboard_id = body_data.pop("dashboard_id") or (await _ensure_default_dashboard(current_user.id, db)).id
    await _get_dashboard_or_404(dashboard_id, current_user.id, db)
    w = DashboardWidget(id=uuid.uuid4(), user_id=current_user.id, dashboard_id=dashboard_id, **body_data)
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
    host_scope = await feed_index.get_user_checkmk_host_scope(db, user_id_str)

    def _host_scope_filter() -> dict | None:
        if not host_scope:
            return None
        return {
            "bool": {
                "should": [
                    {"terms": {"metadata.host.keyword": host_scope}},
                    {"terms": {"metadata.agent.keyword": host_scope}},
                    {"terms": {"metadata.host_candidates.keyword": host_scope}},
                ],
                "minimum_should_match": 1,
            }
        }

    if w.widget_type == "stat":
        os_client = feed_index.get_opensearch()
        query = {"query_string": {"query": query_string or "*"}} if query_string else {"match_all": {}}
        filters = [{
            "bool": {
                "should": [
                    {"terms": {"source": ["checkmk", "graylog", "wazuh"]}},
                    {"bool": {"must": [
                        {"terms": {"source": ["o365", "teams"]}},
                        {"term": {"user_id": user_id_str}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        }]
        if _host_scope_filter():
            filters.append(_host_scope_filter())
        body_query: dict = {
            "bool": {
                "must": [query],
                "filter": filters,
            }
        }
        try:
            resp = await os_client.count(
                index=index_pattern,
                body={"query": body_query},
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
            host_scope=host_scope,
        )
        return {"items": items}

    elif w.widget_type == "donut":
        os_client = feed_index.get_opensearch()
        query = {"query_string": {"query": query_string or "*"}} if query_string else {"match_all": {}}
        filters = [{
            "bool": {
                "should": [
                    {"terms": {"source": ["checkmk", "graylog", "wazuh"]}},
                    {"bool": {"must": [
                        {"terms": {"source": ["o365", "teams"]}},
                        {"term": {"user_id": user_id_str}},
                    ]}},
                ],
                "minimum_should_match": 1,
            }
        }]
        if _host_scope_filter():
            filters.append(_host_scope_filter())
        body = {
            "size": 0,
            "query": {"bool": {"must": [query], "filter": filters}},
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

    elif w.widget_type == "bar":
        os_client = feed_index.get_opensearch()
        agg_field = str(cfg.get("agg_field") or "severity")
        limit = int(cfg.get("limit") or 10)
        # Map friendly field names to actual keyword fields
        field_map = {
            "severity": "severity",
            "source": "source",
            "host": "metadata.host.keyword",
            "metadata.host": "metadata.host.keyword",
            "container": "metadata.container_name.keyword",
            "metadata.container_name": "metadata.container_name.keyword",
            "hostgroup": "metadata.hostgroups.keyword",
        }
        os_field = field_map.get(agg_field, f"{agg_field}.keyword")
        query = {"query_string": {"query": query_string or "*"}} if query_string else {"match_all": {}}
        body = {
            "size": 0,
            "query": {"bool": {"must": [query], "filter": []}},
            "aggs": {"bars": {"terms": {"field": os_field, "size": limit, "order": {"_count": "desc"}}}},
        }
        try:
            resp = await os_client.search(index=index_pattern, body=body, ignore_unavailable=True)
            buckets = [
                {"key": b["key"], "count": b["doc_count"]}
                for b in resp.get("aggregations", {}).get("bars", {}).get("buckets", [])
            ]
            return {"buckets": buckets, "agg_field": agg_field}
        except Exception:
            return {"buckets": [], "agg_field": agg_field}

    elif w.widget_type == "ai_summary":
        from app.models.ai import AiAnalysis
        agent_type = cfg.get("agent_type") or "sysadmin"
        result = await db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.agent_type == agent_type)
            .order_by(AiAnalysis.run_at.desc())
            .limit(1)
        )
        analysis = result.scalar_one_or_none()
        if not analysis:
            return {"analysis_id": None, "summary": "", "findings": [], "recommendations": [], "run_at": None}
        return {
            "analysis_id": str(analysis.id),
            "summary": analysis.severity_summary,
            "findings": [
                {
                    "title":       f.get("title", ""),
                    "severity":    f.get("severity", "info"),
                    "description": f.get("description", ""),
                    "host":        f.get("host") or f.get("affected_service"),
                    "source":      f.get("source", ""),
                }
                for f in (analysis.findings or [])[:5]
            ],
            "recommendations": (analysis.recommendations or [])[:3],
            "run_at": analysis.run_at.isoformat() if analysis.run_at else None,
        }

    elif w.widget_type == "top_hosts":
        limit = int(cfg.get("limit", 8))
        items = await feed_index.search_by_query(
            index_pattern=index_pattern,
            query_string=query_string,
            size=200,
            user_id=user_id_str,
            host_scope=host_scope,
        )
        groups: dict[str, dict] = {}
        for item in items:
            meta = item.get("metadata") or {}
            host = meta.get("host") or meta.get("container_name") or meta.get("agent") or ""
            if not host:
                continue
            if host not in groups:
                groups[host] = {"host": host, "count": 0, "items": [], "external_url": item.get("external_url")}
            groups[host]["count"] += 1
            if len(groups[host]["items"]) < 4:
                groups[host]["items"].append(item)
        hosts = sorted(groups.values(), key=lambda g: g["count"], reverse=True)[:limit]
        return {"hosts": hosts}

    elif w.widget_type == "timeseries":
        from app.services.connectors import get_connector
        from app.core.security import decrypt_credentials
        from app.models.connector import ConnectorConfig as ConnectorModel
        from sqlalchemy import select as sa_select

        data_source = cfg.get("data_source", "prometheus")

        if data_source == "checkmk":
            import asyncio
            cmk_result = await db.execute(
                sa_select(ConnectorModel)
                .where(ConnectorModel.type == "checkmk")
                .where(ConnectorModel.enabled == True)  # noqa: E712
                .limit(1)
            )
            cmk_conn = cmk_result.scalar_one_or_none()
            if not cmk_conn:
                return {"series": [], "unit": "", "error": "No CheckMK connector configured"}

            credentials = decrypt_credentials(cmk_conn.encrypted_credentials)
            connector = get_connector("checkmk", cmk_conn.base_url, credentials)
            service = cfg.get("service", "")
            graph_index = int(cfg.get("graph_index", 0))
            hours = int(cfg.get("hours", 4))

            # Multi-host: hosts list → series_list with one line per host
            hosts: list[str] = cfg.get("hosts") or ([cfg["host"]] if cfg.get("host") else [])
            if not hosts or not service:
                return {"series": [], "unit": "", "error": "host/hosts and service required for CheckMK timeseries"}

            if len(hosts) == 1:
                result = await connector.get_graph_data(hosts[0], service, graph_index, hours)
                if cfg.get("unit"):
                    result["unit"] = cfg["unit"]
                return result

            # Fetch all hosts in parallel
            results = await asyncio.gather(
                *[connector.get_graph_data(h, service, graph_index, hours) for h in hosts],
                return_exceptions=True,
            )
            unit = cfg.get("unit", "")
            series_list = []
            for host, res in zip(hosts, results):
                label = host.split(".")[0]  # cue0111 from cue0111.ippen.media
                if isinstance(res, Exception):
                    series_list.append({"label": label, "series": [], "error": str(res)})
                else:
                    if not unit and res.get("unit"):
                        unit = res["unit"]
                    series_list.append({"label": label, "series": res.get("series", [])})
            return {"series_list": series_list, "unit": unit}

        else:
            # Prometheus-backed timeseries
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
