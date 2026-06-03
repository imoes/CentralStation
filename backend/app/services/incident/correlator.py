"""Incident correlation engine.

After alert aggregation, groups related alerts into Incidents.
Correlation rules (OR, first match wins):
  1. Same primary_host, within a 30-minute window (cross-source bonus)
  2. Same host has alerts from ≥ 2 different sources within 30 minutes

Only creates/extends incidents for high-signal alerts (severity critical/high).
Low/medium alerts are linked to an existing open incident for their host if one exists.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Incident, IncidentMember

log = logging.getLogger(__name__)

_WINDOW_MINUTES = 30
_TRIGGER_SEVERITIES = {"critical", "high"}


def _extract_host(doc: dict) -> str:
    meta = doc.get("metadata") or {}
    return (
        doc.get("host")
        or meta.get("host")
        or meta.get("agent")
        or meta.get("container_name")
        or ""
    ).strip()


async def correlate_docs(docs: list[dict], db: AsyncSession) -> None:
    """Group the given docs into existing or new Incidents."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=_WINDOW_MINUTES)

    # Group docs by host
    by_host: dict[str, list[dict]] = {}
    for doc in docs:
        host = _extract_host(doc)
        if not host:
            continue
        by_host.setdefault(host, []).append(doc)

    for host, host_docs in by_host.items():
        try:
            await _correlate_host(host, host_docs, window_start, now, db)
        except Exception as e:
            log.debug("correlate_docs: host %s failed: %s", host, e)

    try:
        await db.commit()
    except Exception as e:
        log.debug("correlate_docs: commit failed: %s", e)
        await db.rollback()


async def _correlate_host(
    host: str,
    docs: list[dict],
    window_start: datetime,
    now: datetime,
    db: AsyncSession,
) -> None:
    sources = {d.get("source", "") for d in docs}
    severities = {d.get("severity", "info") for d in docs}
    max_severity = next(
        (s for s in ("critical", "high", "medium", "low", "info") if s in severities),
        "info",
    )
    cross_source = len(sources) >= 2

    # Does an open incident for this host already exist in the window?
    existing = await db.execute(
        select(Incident).where(
            and_(
                Incident.primary_host == host,
                Incident.status.in_(("open", "investigating")),
                Incident.created_at >= window_start,
            )
        ).limit(1)
    )
    incident = existing.scalar_one_or_none()

    should_create = (
        incident is None
        and (
            len(docs) >= 2
            or cross_source
            or max_severity in _TRIGGER_SEVERITIES
        )
    )

    if should_create:
        src_list = "/".join(sorted(sources))
        incident = Incident(
            id=uuid.uuid4(),
            title=f"{host}: {len(docs)} Alert(s) [{src_list}]",
            primary_host=host,
            severity=max_severity,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(incident)
        log.info("correlator: new incident %s for host %s (%d docs)", incident.id, host, len(docs))
    elif incident:
        # Update severity if new docs are more severe
        sev_order = ["critical", "high", "medium", "low", "info"]
        current_idx = sev_order.index(incident.severity) if incident.severity in sev_order else 4
        new_idx = sev_order.index(max_severity) if max_severity in sev_order else 4
        if new_idx < current_idx:
            incident.severity = max_severity
        incident.updated_at = now
        log.debug("correlator: extending incident %s for host %s", incident.id, host)

    if incident is None:
        return

    # Add docs as members (skip duplicates)
    existing_members = await db.execute(
        select(IncidentMember.external_id).where(
            IncidentMember.incident_id == incident.id
        )
    )
    existing_ext_ids = {row[0] for row in existing_members}

    for doc in docs:
        ext_id = doc.get("external_id") or doc.get("id") or ""
        if not ext_id or ext_id in existing_ext_ids:
            continue
        member = IncidentMember(
            id=uuid.uuid4(),
            incident_id=incident.id,
            external_id=ext_id,
            source=doc.get("source", ""),
            added_at=now,
        )
        db.add(member)
        existing_ext_ids.add(ext_id)
