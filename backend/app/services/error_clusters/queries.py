"""Cluster lesen — EINE Stelle für API, Dashboard-Widget und Lagebild.

Gefiltert auf den Host-Scope des Nutzers, wie die Mails: wer cue-prod beobachtet, sieht
keine Ausbrüche aus dem Test-Netz.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select


async def in_scope(db, user_id: str, sources: list[str]) -> bool:
    from app.services.error_clusters.incident import host_matches
    from app.services.feed_index import get_user_checkmk_host_scope
    scope = await get_user_checkmk_host_scope(db, user_id)
    return not scope or any(host_matches(s, sources or []) for s in scope)


def summary(ec, t) -> dict:
    from app.services.error_clusters.runner import short_title
    template = t.template if t else ""
    entities = ec.entities or {}
    return {
        "id": str(ec.id),
        "key": ec.key,
        "title": short_title(template) if template else ec.key,
        "template": template,
        "status": ec.status,
        "rules": ec.rules or [],
        "reasons": ec.reasons or [],
        "notify_class": ec.notify_class,
        "started_at": ec.started_at.isoformat(),
        "last_active_at": ec.last_active_at.isoformat(),
        "closed_at": ec.closed_at.isoformat() if ec.closed_at else None,
        "peak15": ec.peak15,
        "total": ec.total,
        "containers": [{"name": k, "count": v} for k, v in
                       sorted(entities.items(), key=lambda kv: -kv[1])[:50]],
        "container_count": len(entities),
        "hosts": ec.sources or [],
        "triage": ec.triage,
        "incident_id": str(ec.incident_id) if ec.incident_id else None,
        "muted": bool(t.muted) if t else False,
        "examples": (t.examples or [])[:3] if t else [],
    }


async def list_for_user(db, user_id: str, *, status: str = "open", hours: int = 24,
                        kind: str = "all", limit: int = 50) -> list[dict]:
    from app.models.error_cluster import ErrorCluster, LogTemplate
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = (select(ErrorCluster, LogTemplate)
         .join(LogTemplate, LogTemplate.key == ErrorCluster.key, isouter=True)
         .where(ErrorCluster.started_at >= since))
    if status != "all":
        q = q.where(ErrorCluster.status == status)
    if kind != "all":
        q = q.where(ErrorCluster.notify_class == kind)
    rows = (await db.execute(q.order_by(ErrorCluster.started_at.desc()).limit(limit * 3))).all()
    out = []
    for ec, t in rows:
        if t is not None and t.muted:
            continue
        if await in_scope(db, user_id, ec.sources or []):
            out.append(summary(ec, t))
        if len(out) >= limit:
            break
    return out


async def series(db, key: str, start: datetime, end: datetime) -> list[dict]:
    """5-min-Zählwerte eines Musters — für Verlauf und Sparkline."""
    from app.models.error_cluster import LogTemplateBucket
    rows = (await db.execute(
        select(LogTemplateBucket.bucket_start, LogTemplateBucket.count)
        .where(LogTemplateBucket.key == key, LogTemplateBucket.bucket_start >= start,
               LogTemplateBucket.bucket_start <= end)
        .order_by(LogTemplateBucket.bucket_start))).all()
    return [{"t": b.isoformat(), "count": c} for b, c in rows]
