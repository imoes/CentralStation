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


async def _collect(db, now: datetime):
    from app.models.alert import Alert
    rows = (await db.execute(select(Alert).where(
        Alert.source == "checkmk", Alert.created_at >= now - WINDOW,
        Alert.status != "resolved"))).scalars().all()
    now_hosts: dict[str, set[str]] = {}
    alerts: dict[str, list[tuple[str, str]]] = {}
    for a in rows:
        meta = a.metadata_ or {}
        svc, host = meta.get("service") or "", meta.get("host") or ""
        if not host:
            continue
        now_hosts.setdefault(svc, set()).add(host)
        alerts.setdefault(svc, []).append((a.external_id or str(a.id), a.severity))
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
            GROUP BY 1, 2"""), {"since": now - BASELINE, "until": now - WINDOW, "svcs": big})
        for svc, _w, n in res.all():
            history.setdefault(svc, []).append(int(n))
    n_windows = int(BASELINE / WINDOW)
    return now_hosts, history, n_windows, alerts


async def run(db, cfg, base_url: str) -> list[dict]:
    """Flotten-Cluster erkennen, Episoden fortführen, Incidents öffnen/schließen.

    Gibt die meldepflichtigen Kontexte zurück (der Runner meldet sie wie Log-Cluster).
    """
    from app.models.error_cluster import ErrorCluster
    from app.services.error_clusters.incident import close_cluster_incident, open_cluster_incident

    now = datetime.now(timezone.utc)
    now_hosts, history, n_windows, alerts = await _collect(db, now)
    hits = {h.service: h for h in evaluate(now_hosts, history, n_windows, alerts)}
    open_eps = {ec.key: ec for ec in (await db.execute(select(ErrorCluster).where(
        ErrorCluster.source == "checkmk_fleet", ErrorCluster.status == "open"))).scalars()}
    to_notify: list[dict] = []

    for service, hit in hits.items():
        key = fleet_key(service)
        ep = open_eps.pop(key, None)
        ctx = {
            "template": f"CheckMK-Service „{service}“ auf {len(hit.hosts)} Hosts zugleich nicht OK",
            "examples": [f"{e} ({s})" for e, s in hit.alerts[:3]],
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
        if ep is None:
            ep = ErrorCluster(id=uuid.uuid4(), key=key, source="checkmk_fleet", status="open",
                              rules=["fleet"], reasons=ctx["reasons"], notify_class="immediate",
                              started_at=now, last_active_at=now, peak15=len(hit.hosts),
                              total=len(hit.alerts), entities={h: 1 for h in hit.hosts},
                              sources=list(hit.hosts), notified_rate=len(hit.hosts),
                              notified_entities=len(hit.hosts), notified_at=now)
            db.add(ep)
            await db.flush()
            title = f"Flotten-Ausfall: {service} · {len(hit.hosts)} Hosts"
            sev = "critical" if any(s == "critical" for _, s in hit.alerts) else "high"
            inc = await open_cluster_incident(
                db, kind="fleet", title=title, severity=sev,
                primary_host=hit.hosts[0], context={"cluster_id": str(ep.id), **ctx},
                member_docs=members)
            ep.incident_id = inc.id
            to_notify.append({"ctx": ctx, "title": title, "cluster_id": str(ep.id),
                              "incident_id": str(inc.id)})
        else:
            ep.last_active_at = now
            ep.peak15 = max(ep.peak15, len(hit.hosts))
            ep.entities = {**(ep.entities or {}), **{h: 1 for h in hit.hosts}}
            ep.sources = sorted(set(ep.sources or []) | set(hit.hosts))
            if ep.incident_id:
                await open_cluster_incident(
                    db, kind="fleet", title=f"Flotten-Ausfall: {service} · {len(ep.entities)} Hosts",
                    severity="high", primary_host=hit.hosts[0],
                    context={"cluster_id": str(ep.id), **ctx}, member_docs=members,
                    existing_id=str(ep.incident_id))
            # Verdopplung der Hosts → erneut melden (höchstens stündlich)
            if (len(hit.hosts) >= 2 * max(ep.notified_entities, 1)
                    and (ep.notified_at is None or now - ep.notified_at >= timedelta(hours=1))):
                ep.notified_entities, ep.notified_at = len(hit.hosts), now
                to_notify.append({"ctx": ctx, "title": f"Flotten-Ausfall: {service} · {len(hit.hosts)} Hosts",
                                  "cluster_id": str(ep.id),
                                  "incident_id": str(ep.incident_id) if ep.incident_id else None})

    for ep in open_eps.values():          # aktuell nicht mehr auffällig
        if now - ep.last_active_at >= QUIET:
            ep.status, ep.closed_at = "closed", now
            if ep.incident_id:
                await close_cluster_incident(db, str(ep.incident_id),
                                             "Service auf weniger Hosts betroffen als üblich, seit 60 min")
    return to_notify
