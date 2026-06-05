"""Incident correlation engine.

After alert aggregation, groups related HIGH-SIGNAL alerts into Incidents.
An Incident is a real, actionable cluster — NOT a relabeled single alert.

Rules (all must hold to create a new incident):
  1. Severity floor: only critical/high alerts can seed or extend an incident.
     low/info alerts are ignored entirely (they are log noise, not incidents).
  2. Minimum size: a new incident needs ≥ 2 correlated alerts (same host within
     the window) OR cross-source evidence (same host, ≥ 2 sources).
  3. Reuse: if an OPEN incident already exists for the host, extend it instead
     of creating a duplicate — regardless of the incident's age (an ongoing
     problem stays one incident until it is resolved).

Resolution is handled by resolve_stale_incidents() (housekeeping job).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Incident, IncidentMember

log = logging.getLogger(__name__)

_WINDOW_MINUTES = 30
_TRIGGER_SEVERITIES = {"critical", "high"}
_SEV_ORDER = ["critical", "high", "medium", "low", "info"]


def _extract_host(doc: dict) -> str:
    meta = doc.get("metadata") or {}
    candidate = (doc.get("host") or meta.get("host") or "").strip()
    # Only accept real FQDNs — reject Docker container short-IDs, bare IPs, etc.
    if candidate and "." in candidate:
        return candidate
    return ""


def _max_severity(severities: set[str]) -> str:
    return next((s for s in _SEV_ORDER if s in severities), "info")


async def correlate_docs(docs: list[dict], db: AsyncSession) -> None:
    """Group high-signal docs into existing or new Incidents."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=_WINDOW_MINUTES)

    # Group docs by host — but only docs that clear the severity floor.
    by_host: dict[str, list[dict]] = {}
    for doc in docs:
        if (doc.get("severity") or "info") not in _TRIGGER_SEVERITIES:
            continue  # low/info never seed an incident
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
    max_severity = _max_severity({d.get("severity", "info") for d in docs})
    cross_source = len(sources) >= 2

    # Reuse an OPEN incident only if it had activity within the window.
    # If the last update was > 30 min ago the incident is closed for new
    # additions — a fresh cluster starts a new incident.
    existing = await db.execute(
        select(Incident).where(
            and_(
                Incident.primary_host == host,
                Incident.status.in_(("open", "investigating")),
                Incident.updated_at >= window_start,
            )
        ).order_by(Incident.updated_at.desc()).limit(1)
    )
    incident = existing.scalar_one_or_none()

    if incident is None:
        # New incident requires a real cluster: ≥2 alerts OR cross-source.
        # A lone critical alert is just an alert — it lives in the feed,
        # and becomes an incident only once it correlates with something.
        if len(docs) < 2 and not cross_source:
            return
        src_list = "/".join(sorted(s for s in sources if s))
        incident = Incident(
            id=uuid.uuid4(),
            title=f"{host}: {len(docs)} Alerts [{src_list}]",
            primary_host=host,
            severity=max_severity,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(incident)
        log.info("correlator: new incident %s for %s (%d docs, %s)",
                 incident.id, host, len(docs), src_list)
    else:
        # Escalate severity if the new docs are more severe.
        cur = _SEV_ORDER.index(incident.severity) if incident.severity in _SEV_ORDER else 4
        new = _SEV_ORDER.index(max_severity) if max_severity in _SEV_ORDER else 4
        if new < cur:
            incident.severity = max_severity
        incident.updated_at = now

    # Add new members (skip duplicates).
    existing_members = await db.execute(
        select(IncidentMember.external_id).where(
            IncidentMember.incident_id == incident.id
        )
    )
    existing_ext_ids = {row[0] for row in existing_members}

    added = 0
    for doc in docs:
        ext_id = doc.get("external_id") or doc.get("id") or ""
        if not ext_id or ext_id in existing_ext_ids:
            continue
        db.add(IncidentMember(
            id=uuid.uuid4(),
            incident_id=incident.id,
            external_id=ext_id,
            source=doc.get("source", ""),
            added_at=now,
        ))
        existing_ext_ids.add(ext_id)
        added += 1

    # Refresh title to reflect the current member count.
    total = len(existing_ext_ids)
    src_list = "/".join(sorted(s for s in sources if s))
    incident.title = f"{host}: {total} Alerts [{src_list}]"
    if added:
        log.debug("correlator: +%d members on incident %s (%d total)",
                  added, incident.id, total)


async def resolve_stale_incidents(db: AsyncSession) -> int:
    """Auto-resolve incidents whose alerts are all resolved or stale.

    An incident is resolved when:
      - none of its member alerts are still open in OpenSearch, OR
      - the incident has had no new member for > 2 hours (stale).

    Returns the number of incidents resolved. Run from the housekeeping job.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=2)
    resolved = 0

    rows = await db.execute(
        select(Incident).where(Incident.status.in_(("open", "investigating")))
    )
    incidents = rows.scalars().all()
    if not incidents:
        return 0

    from app.services.feed_index import search_by_query

    for inc in incidents:
        try:
            # Stale: no activity for 2h → close.
            if inc.updated_at and inc.updated_at < stale_cutoff:
                inc.status = "resolved"
                inc.resolved_at = now
                resolved += 1
                continue

            # Otherwise check whether any member alert is still open.
            mem = await db.execute(
                select(IncidentMember.external_id).where(
                    IncidentMember.incident_id == inc.id
                )
            )
            ext_ids = [r[0] for r in mem]
            if not ext_ids:
                inc.status = "resolved"
                inc.resolved_at = now
                resolved += 1
                continue

            q = " OR ".join(f'external_id:"{e}"' for e in ext_ids[:30])
            try:
                open_items = await search_by_query(
                    "cs-feed-*",
                    f"({q}) AND NOT status:resolved",
                    size=1,
                )
            except Exception:
                open_items = [1]  # on query error, keep incident open (fail-safe)

            if not open_items:
                inc.status = "resolved"
                inc.resolved_at = now
                resolved += 1
        except Exception as e:
            log.debug("resolve_stale_incidents: incident %s failed: %s", inc.id, e)

    if resolved:
        try:
            await db.commit()
            log.info("resolve_stale_incidents: resolved %d incidents", resolved)
        except Exception as e:
            log.debug("resolve_stale_incidents: commit failed: %s", e)
            await db.rollback()
    return resolved
