"""Incidents aus der Cluster-Erkennung — ersetzt den früheren Host-Correlator.

Eine Quelle für Incidents, drei Arten (`incidents.kind`):

* ``host``        — mehrere hochwertige Signale auf EINEM Host. Regel unverändert aus
                    dem Host-Correlator: nur critical/high; neu ab ≥ 2 Alerts oder ≥ 2
                    Quellen; ein offener Incident desselben Hosts mit Aktivität in den
                    letzten 30 min wird erweitert.
* ``log_cluster`` — ein Log-Muster über viele Container (Engine-Episode, Sofort-Meldung).
* ``fleet``       — ein CheckMK-Service auf vielen Hosts zugleich.

**Ein Vorfall, ein Incident:** Gehört ein Host zu einem offenen übergreifenden Cluster
(log_cluster/fleet), werden seine Alerts dort Mitglied, statt daneben einen
host-Incident zu eröffnen. Öffnet ein übergreifender Cluster, gehen die offenen
host-Incidents seiner Hosts darin auf.

Den Cluster-Kontext (Muster, Verlauf, Container, Graylog-Abfrage, KI-Einschätzung) trägt
ein Eintrag ``{"type": "error_cluster", …}`` in ``causal_context``; die AIKB-Ursachensuche
(causal_correlator) ergänzt daneben, ohne ihn zu überschreiben.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Incident, IncidentMember

log = logging.getLogger(__name__)

HOST_WINDOW_MINUTES = 30
TRIGGER_SEVERITIES = {"critical", "high"}
SEV_ORDER = ["critical", "high", "medium", "low", "info"]
CROSS_KINDS = ("log_cluster", "fleet")
OPEN = ("open", "investigating")


# ── reine Helfer (aus dem Host-Correlator übernommen) ────────────────────────

def extract_host(doc: dict) -> str:
    meta = doc.get("metadata") or {}
    candidate = (doc.get("host") or meta.get("host") or "").strip()
    # Nur echte FQDNs — keine Container-Kurz-IDs, keine nackten IPs.
    return candidate if candidate and "." in candidate else ""


def max_severity(severities: set[str]) -> str:
    return next((s for s in SEV_ORDER if s in severities), "info")


def checkmk_incident_is_inactive(members: list[tuple[str, str]],
                                 active_external_ids: set[str]) -> bool:
    """True nur für einen reinen CheckMK-Incident ohne aktives Mitglied.

    Gemischte Incidents lassen sich aus einem CheckMK-Abruf nicht entscheiden und
    bleiben für den allgemeinen Abgleich (resolve_inactive_host_incidents).
    """
    return (bool(members) and all(source == "checkmk" for _, source in members)
            and not any(ext in active_external_ids for ext, _ in members))


def cluster_context(inc: Incident) -> dict | None:
    for entry in inc.causal_context or []:
        if isinstance(entry, dict) and entry.get("type") == "error_cluster":
            return entry
    return None


def host_matches(host: str, cluster_hosts: list[str]) -> bool:
    """FQDN gegen Kurznamen: "mu-img-docker19.example.com" ≙ "mu-img-docker19"."""
    h = host.lower()
    short = h.split(".", 1)[0]
    for c in cluster_hosts or []:
        c = (c or "").lower()
        if c and (c == h or c.split(".", 1)[0] == short):
            return True
    return False


# ── Mitglieder ───────────────────────────────────────────────────────────────

async def _add_members(db: AsyncSession, incident: Incident, docs: list[dict],
                       now: datetime) -> int:
    rows = await db.execute(select(IncidentMember.external_id)
                            .where(IncidentMember.incident_id == incident.id))
    have = {r[0] for r in rows}
    added = 0
    for doc in docs:
        ext = doc.get("external_id") or doc.get("id") or ""
        if not ext or ext in have:
            continue
        db.add(IncidentMember(id=uuid.uuid4(), incident_id=incident.id, external_id=ext,
                              source=doc.get("source", ""), added_at=now))
        have.add(ext)
        added += 1
    return added


async def _open_cross_incidents(db: AsyncSession) -> list[Incident]:
    rows = await db.execute(select(Incident).where(
        Incident.kind.in_(CROSS_KINDS), Incident.status.in_(OPEN)))
    return list(rows.scalars().all())


# ── host: ersetzt correlator.correlate_docs ──────────────────────────────────

async def process_alerts(docs: list[dict], db: AsyncSession) -> None:
    """Neue Alerts aus der Aggregation einordnen (vorher correlator.correlate_docs)."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=HOST_WINDOW_MINUTES)

    by_host: dict[str, list[dict]] = {}
    for doc in docs:
        if (doc.get("severity") or "info") not in TRIGGER_SEVERITIES:
            continue
        meta = doc.get("metadata") or {}
        if meta.get("kind") == "error_cluster":
            continue            # das Cluster-Dokument selbst ist schon ein Incident
        host = extract_host(doc)
        if host:
            by_host.setdefault(host, []).append(doc)
    if not by_host:
        return

    cross = await _open_cross_incidents(db)
    new_incidents: list[tuple[str, str]] = []
    for host, host_docs in by_host.items():
        try:
            # Vorrang: gehört der Host zu einem laufenden übergreifenden Cluster, sind
            # seine Alerts Teil DIESES Vorfalls — kein zweiter Incident daneben.
            owner = next((i for i in cross
                          if host_matches(host, (cluster_context(i) or {}).get("hosts", []))),
                         None)
            if owner is not None:
                if await _add_members(db, owner, host_docs, now):
                    owner.updated_at = now
                continue
            inc = await _host_incident(host, host_docs, window_start, now, db)
            if inc is not None:
                await db.flush()
                new_incidents.append((host, str(inc.id)))
        except Exception as exc:
            log.debug("process_alerts: host %s failed: %s", host, exc)

    try:
        await db.commit()
    except Exception as exc:
        log.debug("process_alerts: commit failed: %s", exc)
        await db.rollback()
        return

    if new_incidents:
        try:
            from app.services.incident.causal_correlator import enrich_incident_causal_context
            for host, inc_id in new_incidents:
                await enrich_incident_causal_context(inc_id, host, db)
            await db.commit()
        except Exception as exc:
            log.debug("process_alerts: causal enrichment failed: %s", exc)


async def _host_incident(host: str, docs: list[dict], window_start: datetime,
                         now: datetime, db: AsyncSession) -> Incident | None:
    """Erstellt oder erweitert einen host-Incident. Gibt einen NEUEN Incident zurück."""
    sources = {d.get("source", "") for d in docs}
    sev = max_severity({d.get("severity", "info") for d in docs})
    existing = await db.execute(select(Incident).where(and_(
        Incident.primary_host == host,
        Incident.kind == "host",
        Incident.status.in_(OPEN),
        Incident.updated_at >= window_start,
    )).order_by(Incident.updated_at.desc()).limit(1))
    incident = existing.scalar_one_or_none()
    is_new = False
    if incident is None:
        # Ein einzelner kritischer Alert ist ein Alert, kein Incident.
        if len(docs) < 2 and len(sources) < 2:
            return None
        incident = Incident(id=uuid.uuid4(), title="", primary_host=host, severity=sev,
                            status="open", created_at=now, updated_at=now, kind="host")
        db.add(incident)
        is_new = True
    else:
        if SEV_ORDER.index(sev) < SEV_ORDER.index(incident.severity
                                                  if incident.severity in SEV_ORDER else "info"):
            incident.severity = sev
        incident.updated_at = now
    await _add_members(db, incident, docs, now)
    rows = await db.execute(select(IncidentMember.external_id)
                            .where(IncidentMember.incident_id == incident.id))
    total = len({r[0] for r in rows} | {d.get("external_id") or d.get("id") for d in docs})
    incident.title = f"{host}: {total} Alerts [{'/'.join(sorted(s for s in sources if s))}]"
    return incident if is_new else None


# ── log_cluster / fleet: aus Engine-Episoden ─────────────────────────────────

async def open_cluster_incident(db: AsyncSession, *, kind: str, title: str, severity: str,
                                primary_host: str, context: dict, member_docs: list[dict],
                                existing_id: str | None = None) -> Incident:
    """Incident für eine übergreifende Episode anlegen oder (bei reopen) wieder öffnen.

    Offene host-Incidents der betroffenen Hosts gehen darin auf: ihre Mitglieder werden
    übernommen, sie selbst mit Verweis geschlossen — ein Vorfall, ein Incident.
    """
    now = datetime.now(timezone.utc)
    inc = None
    if existing_id:
        inc = (await db.execute(select(Incident).where(Incident.id == uuid.UUID(existing_id)))
               ).scalar_one_or_none()
    if inc is None:
        inc = Incident(id=uuid.uuid4(), title=title[:300], primary_host=primary_host or "",
                       severity=severity, status="open", created_at=now, updated_at=now,
                       kind=kind)
        db.add(inc)
        await db.flush()
    else:
        inc.status, inc.resolved_at, inc.updated_at = "open", None, now
        inc.title, inc.severity = title[:300], severity
    set_cluster_context(inc, context)
    await _add_members(db, inc, member_docs, now)

    hosts = context.get("hosts", [])
    if hosts:
        rows = await db.execute(select(Incident).where(
            Incident.kind == "host", Incident.status.in_(OPEN)))
        for h in rows.scalars().all():
            if h.id == inc.id or not host_matches(h.primary_host, hosts):
                continue
            mem = await db.execute(select(IncidentMember.external_id, IncidentMember.source)
                                   .where(IncidentMember.incident_id == h.id))
            await _add_members(db, inc, [{"external_id": e, "source": s} for e, s in mem], now)
            h.status, h.resolved_at, h.updated_at = "resolved", now, now
            ctx = list(h.causal_context or [])
            ctx.append({"type": "merged_into", "incident_id": str(inc.id),
                        "reason": "aufgegangen im übergreifenden Fehler-Cluster"})
            h.causal_context = ctx
    return inc


def set_cluster_context(inc: Incident, context: dict) -> None:
    entries = [e for e in (inc.causal_context or [])
               if not (isinstance(e, dict) and e.get("type") == "error_cluster")]
    inc.causal_context = [{"type": "error_cluster", **context}] + entries


async def close_cluster_incident(db: AsyncSession, incident_id: str, reason: str) -> None:
    inc = (await db.execute(select(Incident).where(Incident.id == uuid.UUID(incident_id)))
           ).scalar_one_or_none()
    if inc is None or inc.status == "resolved":
        return
    now = datetime.now(timezone.utc)
    inc.status, inc.resolved_at, inc.updated_at = "resolved", now, now
    ctx = cluster_context(inc) or {}
    set_cluster_context(inc, {**{k: v for k, v in ctx.items() if k != "type"},
                              "resolved_reason": reason})


# ── Auflösen: ersetzt resolve_inactive_checkmk_incidents / resolve_stale_incidents ──

async def close_recovered(active_external_ids: set[str], db: AsyncSession) -> tuple[int, set[str]]:
    """Reine CheckMK-host-Incidents im selben Aggregationslauf schließen, in dem ihr
    letzter Check wieder OK ist — sonst sähe das generative Dashboard einen Zwischenstand."""
    now = datetime.now(timezone.utc)
    rows = await db.execute(select(Incident).where(Incident.kind == "host",
                                                   Incident.status.in_(OPEN)))
    resolved, hosts = 0, set()
    for inc in rows.scalars().all():
        mem = await db.execute(select(IncidentMember.external_id, IncidentMember.source)
                               .where(IncidentMember.incident_id == inc.id))
        if not checkmk_incident_is_inactive([(r[0], r[1]) for r in mem], active_external_ids):
            continue
        inc.status, inc.resolved_at, inc.updated_at = "resolved", now, now
        resolved += 1
        if inc.primary_host:
            hosts.add(inc.primary_host)
    if resolved:
        await db.commit()
        log.info("close_recovered: %d host incident(s) after CheckMK recovery", resolved)
    return resolved, hosts


async def resolve_inactive_host_incidents(db: AsyncSession) -> int:
    """host-Incidents schließen, deren Mitglieder alle erledigt sind (Sicherheitsnetz).

    Übergreifende Cluster schließt ihre Episode (60 min Ruhe), nicht dieser Abgleich.
    """
    now = datetime.now(timezone.utc)
    rows = await db.execute(select(Incident).where(Incident.kind == "host",
                                                   Incident.status.in_(OPEN)))
    incidents = rows.scalars().all()
    if not incidents:
        return 0
    from app.services.feed_index import search_by_query
    resolved = 0
    for inc in incidents:
        try:
            mem = await db.execute(select(IncidentMember.external_id)
                                   .where(IncidentMember.incident_id == inc.id))
            ext_ids = [r[0] for r in mem]
            if ext_ids:
                q = " OR ".join(f'external_id:"{e}"' for e in ext_ids[:30])
                try:
                    still_open = await search_by_query(
                        "cs-feed-*", f"({q}) AND NOT status:resolved", size=1)
                except Exception:
                    still_open = [1]     # bei Fehler offen lassen
                if still_open:
                    continue
            inc.status, inc.resolved_at = "resolved", now
            resolved += 1
        except Exception as exc:
            log.debug("resolve_inactive_host_incidents: %s failed: %s", inc.id, exc)
    if resolved:
        await db.commit()
        log.info("resolve_inactive_host_incidents: resolved %d", resolved)
    return resolved
