"""CheckMK-Flotten-Cluster: derselbe Service fällt auf vielen Hosts zugleich aus.

Ein CheckMK-Alert entsteht, wenn ein Problem BEGINNT. Gezählt wird je Service, auf wie
vielen Hosts in den letzten 15 min ein Problem neu begann — gegen das Übliche der
letzten 7 Tage (Median je 15-min-Fenster, Nullen eingeschlossen). Eine feste Schwelle
ginge nicht: "Check_MK" betraf in 7 Tagen 542 Hosts, für diesen Service sind 10 neue
Probleme in 15 min normal.

Ergebnis ist ein Incident der Art "fleet" (ein Vorfall, viele Hosts); Host-Alerts der
betroffenen Hosts werden dort Mitglied statt eigene Incidents (incident.process_alerts).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.services.error_clusters.rules import DEFAULT, Thresholds, fleet_cluster, median_with_zeros

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=15)
BASELINE = timedelta(days=7)
QUIET = timedelta(minutes=60)
#: Dienste, die selbst nur Folge eines anderen Problems sind (Agent nicht erreichbar →
#: alle Checks des Hosts) — sie sind kein eigener Flotten-Ausfall.
SKIP_SERVICES = {"", "Check_MK Discovery"}


def fleet_key(service: str) -> str:
    return hashlib.md5(f"fleet:{service}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class FleetHit:
    service: str
    hosts: tuple[str, ...]
    usual: float
    alerts: tuple[tuple[str, str], ...]     # (external_id, severity)


def evaluate(now_hosts: dict[str, set[str]], history: dict[str, list[int]], n_windows: int,
             alerts: dict[str, list[tuple[str, str]]], th: Thresholds = DEFAULT) -> list[FleetHit]:
    """Reine Bewertung: welcher Service ist gerade auf ungewöhnlich vielen Hosts betroffen.

    history[service]: Host-Anzahlen der 15-min-Fenster der letzten 7 Tage, in denen der
    Service überhaupt betroffen war (der Rest zählt als 0, siehe median_with_zeros).
    """
    hits = []
    for service, hosts in now_hosts.items():
        if service in SKIP_SERVICES:
            continue
        usual = median_with_zeros(history.get(service, []), n_windows)
        if fleet_cluster(len(hosts), usual, th):
            hits.append(FleetHit(service, tuple(sorted(hosts)), usual,
                                 tuple(alerts.get(service, []))))
    return sorted(hits, key=lambda h: -len(h.hosts))


def group_leader(hosts: set[str], candidates: list[tuple[str, datetime, set[str]]],
                 now: datetime, th: Thresholds = DEFAULT) -> str | None:
    """Incident eines schon gemeldeten Flotten-Ausfalls, zu dem dieser gehört — oder None.

    candidates: (incident_id, Beginn, Hosts) offener, gemeldeter Flotten-Episoden.
    Dieselbe Regel wie bei Log-Mustern (engine._group_leader): begann höchstens
    group_minutes vorher und teilt Hosts (Jaccard ≥ group_overlap). Beispiel 01.10.
    22:36: ORD1, ORD2 und ORD3 fielen auf denselben 5 Hosts aus — ein Vorfall, nicht
    drei Mails mit drei Formulierungen derselben Einschätzung.
    """
    best, best_score = None, 0.0
    for inc_id, started, theirs in candidates:
        if not inc_id or now - started > timedelta(minutes=th.group_minutes):
            continue
        union = hosts | theirs
        score = len(hosts & theirs) / len(union) if union else 0.0
        if score >= th.group_overlap and score > best_score:
            best, best_score = inc_id, score
    return best


def worth_renotify(hosts_now: int, notified_hosts: int, notified_at: datetime | None,
                   now: datetime, th: Thresholds = DEFAULT) -> bool:
    """Erneut melden nur bei Verdopplung der Hosts, höchstens einmal je renotify_min_minutes."""
    return (hosts_now >= th.renotify_factor * max(notified_hosts, 1)
            and (notified_at is None
                 or now - notified_at >= timedelta(minutes=th.renotify_min_minutes)))


async def _collect(db, now: datetime):
    """Probleme, die vor min_age bis min_age+15 min BEGANNEN und JETZT noch offen sind.

    Bis 2026-10-12 zählten Probleme ab der ersten Minute — ein Proxmox-Neustart oder der
    nächtliche Backup-Job machte für Minuten viele Hosts rot, und die Mail war raus,
    bevor sich alles von selbst erholt hatte. Jetzt muss ein Problem min_age_minutes
    überstehen (ein noch offener Alarm dieses Alters hat das nachweislich).
    """
    from app.models.alert import Alert
    aged = now - timedelta(minutes=DEFAULT.min_age_minutes)
    rows = (await db.execute(select(Alert).where(
        Alert.source == "checkmk", Alert.created_at >= aged - WINDOW,
        Alert.created_at < aged, Alert.status != "resolved"))).scalars().all()
    now_hosts: dict[str, set[str]] = {}
    alerts: dict[str, list[tuple[str, str]]] = {}
    outputs: dict[str, list[str]] = {}
    for a in rows:
        meta = a.metadata_ or {}
        svc, host = meta.get("service") or "", meta.get("host") or ""
        if not host:
            continue
        now_hosts.setdefault(svc, set()).add(host)
        alerts.setdefault(svc, []).append((a.external_id or str(a.id), a.severity))
        # Die CheckMK-Ausgabe trägt die gemeinsame Ursache ("from source 'vpp0252'") —
        # ohne sie riet die KI-Einschätzung nur auf "Kommunikationsstörung".
        out = (a.body or "").strip().split("\n", 1)[0][:300]
        if out and out not in outputs.setdefault(svc, []) and len(outputs[svc]) < 5:
            outputs[svc].append(f"{host}: {out}")
    big = [s for s, h in now_hosts.items() if len(h) >= DEFAULT.fleet_min_hosts]
    history: dict[str, list[int]] = {}
    if big:
        res = await db.execute(text("""
            SELECT metadata->>'service' AS svc,
                   date_bin('15 minutes', created_at, TIMESTAMPTZ '2000-01-01') AS w,
                   count(DISTINCT metadata->>'host') AS hosts
            FROM alerts
            WHERE source = 'checkmk' AND created_at >= :since AND created_at < :until
              AND metadata->>'service' = ANY(:svcs)
            GROUP BY 1, 2"""), {"since": aged - BASELINE, "until": aged - WINDOW, "svcs": big})
        for svc, _w, n in res.all():
            history.setdefault(svc, []).append(int(n))
    n_windows = int(BASELINE / WINDOW)
    return now_hosts, history, n_windows, alerts, outputs


async def run(db, cfg, base_url: str) -> list[dict]:
    """Flotten-Cluster erkennen, Episoden fortführen, Incidents öffnen/schließen.

    Gibt die meldepflichtigen Kontexte zurück (der Runner meldet sie wie Log-Cluster).
    """
    from app.models.error_cluster import ErrorCluster
    from app.services.error_clusters.incident import (
        add_pattern, close_cluster_incident, open_cluster_incident, reopen_incident,
    )

    now = datetime.now(timezone.utc)
    now_hosts, history, n_windows, alerts, outputs = await _collect(db, now)
    hits = {h.service: h for h in evaluate(now_hosts, history, n_windows, alerts)}
    open_eps = {ec.key: ec for ec in (await db.execute(select(ErrorCluster).where(
        ErrorCluster.source == "checkmk_fleet", ErrorCluster.status == "open"))).scalars()}
    to_notify: list[dict] = []

    # Gemeldete offene Episoden als Gruppierungs-Kandidaten (Hits kommen nach Größe
    # sortiert — der größte gleichzeitige Ausfall wird zum Leitvorfall).
    candidates = [(str(e.incident_id) if e.incident_id else None, e.started_at,
                   set(e.sources or [])) for e in open_eps.values()]

    for service, hit in hits.items():
        key = fleet_key(service)
        ep = open_eps.pop(key, None)
        reopened = False
        if ep is None:
            # Innerhalb des Cooldowns geht die ALTE Episode weiter, wie bei Log-Mustern.
            # Ohne das wurde "Check_MK" (Ursache vpp0252) am 01./02.10. sechsmal als neuer
            # Vorfall gemeldet — jeweils 5–30 min nach dem Schließen.
            ep = (await db.execute(select(ErrorCluster).where(
                ErrorCluster.source == "checkmk_fleet", ErrorCluster.key == key,
                ErrorCluster.status == "closed",
                ErrorCluster.closed_at >= now - timedelta(hours=DEFAULT.cooldown_hours))
                .order_by(ErrorCluster.closed_at.desc()).limit(1))).scalar_one_or_none()
            if ep is not None:
                ep.status, ep.closed_at, reopened = "open", None, True
        ctx = {
            "template": f"CheckMK-Service „{service}“ auf {len(hit.hosts)} Hosts zugleich nicht OK",
            "examples": outputs.get(service) or [f"{e} ({s})" for e, s in hit.alerts[:3]],
            "rules": ["fleet"],
            "reasons": [f"{len(hit.hosts)} Hosts in 15 min, üblich {max(hit.usual, 1):.0f}"],
            "count15": len(hit.hosts), "expected15": hit.usual, "total": len(hit.alerts),
            "started_at": (ep.started_at if ep else now).isoformat(),
            "until": now.isoformat(),
            "containers": [{"name": h, "count": 1} for h in hit.hosts],
            "hosts": list(hit.hosts),
            "graylog_url": None,
            "service": service,
        }
        members = [{"external_id": e, "source": "checkmk"} for e, _ in hit.alerts]
        pattern = {"template_key": key, "template": ctx["template"], "count15": len(hit.hosts),
                   "total": len(hit.alerts), "rules": ["fleet"],
                   "started_at": ctx["started_at"], "hosts": list(hit.hosts),
                   "containers": ctx["containers"][:15], "examples": ctx["examples"][:2]}
        # Musterzeile wie bei Log-Clustern — für Titel, Stummschalten und Tagesübersicht.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.error_cluster import LogTemplate
        await db.execute(pg_insert(LogTemplate).values(
            key=key, template=f"CheckMK-Service {service}", examples=ctx["examples"],
            first_seen=now, last_seen=now, muted=False,
        ).on_conflict_do_update(index_elements=["key"], set_={"last_seen": now}))
        muted = (await db.execute(select(LogTemplate.muted).where(LogTemplate.key == key))).scalar()
        if muted:
            continue
        title = f"Flotten-Ausfall: {service} · {len(hit.hosts)} Hosts"
        if ep is None:
            ep = ErrorCluster(id=uuid.uuid4(), key=key, source="checkmk_fleet", status="open",
                              rules=["fleet"], reasons=ctx["reasons"], notify_class="immediate",
                              started_at=now, last_active_at=now, peak15=len(hit.hosts),
                              total=len(hit.alerts), entities={h: 1 for h in hit.hosts},
                              sources=list(hit.hosts))
            db.add(ep)
            await db.flush()
            leader_inc = group_leader(set(hit.hosts), candidates, now)
            if leader_inc:
                # Teil eines schon gemeldeten Ausfalls: Muster im Leit-Incident, keine Mail.
                ep.incident_id = uuid.UUID(leader_inc)
                await add_pattern(db, leader_inc, pattern, members)
                continue
            ep.notified_rate = ep.notified_entities = len(hit.hosts)
            ep.notified_at = now
            sev = "critical" if any(s == "critical" for _, s in hit.alerts) else "high"
            inc = await open_cluster_incident(
                db, kind="fleet", title=title, severity=sev,
                primary_host=hit.hosts[0], context={"cluster_id": str(ep.id), **ctx},
                member_docs=members)
            ep.incident_id = inc.id
            candidates.append((str(inc.id), now, set(hit.hosts)))
            to_notify.append({"ctx": ctx, "title": title, "cluster_id": str(ep.id),
                              "incident_id": str(inc.id)})
            continue

        ep.last_active_at = now
        ep.peak15 = max(ep.peak15, len(hit.hosts))
        ep.entities = {**(ep.entities or {}), **{h: 1 for h in hit.hosts}}
        ep.sources = sorted(set(ep.sources or []) | set(hit.hosts))
        leader = ep.incident_id is not None and await _leads_incident(db, ep)
        if ep.incident_id and leader:
            await open_cluster_incident(
                db, kind="fleet", title=f"Flotten-Ausfall: {service} · {len(ep.entities)} Hosts",
                severity="high", primary_host=hit.hosts[0],
                context={"cluster_id": str(ep.id), **ctx}, member_docs=members,
                existing_id=str(ep.incident_id))
        elif ep.incident_id:
            await add_pattern(db, str(ep.incident_id), pattern, members)
            if reopened:
                await reopen_incident(db, str(ep.incident_id))
            continue                         # Teil eines Ausfalls meldet nie selbst
        # Verdopplung der Hosts → erneut melden (höchstens stündlich)
        if worth_renotify(len(hit.hosts), ep.notified_entities, ep.notified_at, now):
            ep.notified_entities, ep.notified_at = len(hit.hosts), now
            to_notify.append({"ctx": ctx, "title": title, "cluster_id": str(ep.id),
                              "incident_id": str(ep.incident_id) if ep.incident_id else None})

    for ep in open_eps.values():          # aktuell nicht mehr auffällig
        if now - ep.last_active_at >= QUIET:
            ep.status, ep.closed_at = "closed", now
            if ep.incident_id:
                others = (await db.execute(select(ErrorCluster.id).where(
                    ErrorCluster.incident_id == ep.incident_id, ErrorCluster.id != ep.id,
                    ErrorCluster.status == "open"))).first()
                if others is None:
                    await close_cluster_incident(db, str(ep.incident_id),
                                                 "Service auf weniger Hosts betroffen als üblich, seit 60 min")
    return to_notify


async def _leads_incident(db, ep) -> bool:
    """Ist diese Episode der Leitvorfall ihres Incidents (oder nur ein weiteres Muster)?"""
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import cluster_context
    inc = (await db.execute(select(Incident).where(Incident.id == ep.incident_id))
           ).scalar_one_or_none()
    return inc is not None and (cluster_context(inc) or {}).get("cluster_id") == str(ep.id)
