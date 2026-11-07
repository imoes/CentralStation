"""Ein Incident ist ein Objekt der Suche (Nutzer, 07.11.: „Ein Incident ist jetzt auch ein Objekt der Suche
und Filter?“ — gemessen: kein Dokument in cs-logs-* trug einen Bezug zu einem Incident).

Wie jedes Objekt ist ein Incident eine Suche in der einen Filtersprache (filter.py) — sein `filter` sagt,
welche Nachrichten zu ihm gehören, im Zeitfenster des Incidents:
  log_cluster  muster:<Drain-Schlüssel> seiner Muster (über alle Hosts — wie die Fehler-Cluster-Episode)
  fleet        dienst:"<Service>" auf seinen Hosts
  critical     host:<Host> signal:schaden (kritische Kernelmeldung)
  host         host:<Host>
Was der Filter trifft, trägt den Hashtag `incident:<id>` — derselbe Name wie an Konsolen-Sitzungen
(konsole.py). Damit ist der Incident Filter, Facette und Knoten im Fehler-Netzplan.

Markiert werden nur aktive Incidents (offen und in AKTIV_STUNDEN aktualisiert, oder in diesem Zeitraum
gelöst): gemessen 07.11. standen 1 285 Host-Incidents auf „offen“, aber nur 146 waren in 6 h aktualisiert
— der Rest hängt im Limbo (Lebenszyklus: Schritt 3/4 des Umbaus).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

AKTIV_STUNDEN = 6
#: so lange vor dem Anlegen gehören Nachrichten schon zum Incident (er entsteht erst nach einem Ausbruch)
VORLAUF = timedelta(minutes=15)
#: höchstens so weit zurück wird je Lauf markiert
RUECKBLICK = timedelta(hours=24)
#: Arten, aus denen ein Filter entsteht — erschöpfend; jede andere ist „ohne Filter“ (benannt)
ARTEN = ("log_cluster", "fleet", "critical", "host")


def _tag(art: str, wert: str) -> str:
    wert = (wert or "").replace('"', "")
    return f'{art}:"{wert}"' if any(c in wert for c in " ()\t") or wert.endswith("*") else f"{art}:{wert}"


def _oder(tags: list[str]) -> str:
    tags = sorted(set(t for t in tags if t))
    return tags[0] if len(tags) == 1 else "(" + " OR ".join(tags) + ")"


def filter_von(kind: str, primary_host: str, ctx: dict | None, kanon=lambda h: h) -> str | None:
    """Der Incident als Suche. Rein — testbar. `kanon` bildet Hostnamen auf die Namen der Historie ab."""
    ctx = ctx or {}
    if kind == "log_cluster":
        keys = [ctx.get("template_key")] + [p.get("template_key") for p in ctx.get("patterns") or []
                                            if isinstance(p, dict)]
        keys = [k for k in keys if k]
        return _oder([_tag("muster", k) for k in keys]) if keys else None
    if kind == "fleet":
        if not ctx.get("service"):
            return None
        hosts = [kanon(h) for h in ctx.get("hosts") or [] if h]
        return _tag("dienst", ctx["service"]) + (" " + _oder([_tag("host", h) for h in hosts]) if hosts else "")
    if kind == "critical" and primary_host:
        return f"{_tag('host', kanon(primary_host))} signal:schaden"
    if kind == "host" and primary_host:
        return _tag("host", kanon(primary_host))
    return None


def fenster(created_at: datetime, resolved_at: datetime | None, jetzt: datetime) -> tuple[datetime, datetime]:
    """Zeitfenster der Nachrichten eines Incidents, für einen Lauf begrenzt auf RUECKBLICK."""
    seit = max(created_at - VORLAUF, jetzt - RUECKBLICK)
    return seit, (resolved_at or jetzt)


async def markiere(incident_id: str, filtertext: str, seit: datetime, bis: datetime) -> int:
    """Nachrichten des Incidents tragen `incident:<id>` (idempotent). Rückgabe: neu markierte."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    from app.services.log_watch.historie import PRAEFIX
    tag = f"incident:{incident_id}"
    r = await get_opensearch().update_by_query(
        index=f"{PRAEFIX}*", conflicts="proceed", refresh=True, body={
            "query": {"bool": {"filter": [flt.opensearch(filtertext),
                                          {"range": {"zeit": {"gte": seit.isoformat(), "lte": bis.isoformat()}}}],
                               "must_not": [{"term": {"hashtags": tag}}, {"terms": {"quelle": ["ki", "konsole"]}}]}},
            "script": {"lang": "painless", "params": {"t": tag},
                       "source": "if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
                                 " ctx._source.hashtags.add(params.t);"}})
    return int(r.get("updated") or 0)


async def lauf(jetzt: datetime | None = None) -> dict:
    """Alle aktiven Incidents markieren. Ergebnis je Lauf (raster_lauf „incident-objekte“)."""
    from sqlalchemy import or_, select

    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import cluster_context
    from app.services.log_watch.hosts import verzeichnis

    jetzt = jetzt or datetime.now(timezone.utc)
    grenze = jetzt - timedelta(hours=AKTIV_STUNDEN)
    async with AsyncSessionLocal() as db:
        verz = await verzeichnis(db)
        incs = (await db.execute(select(Incident).where(or_(
            (Incident.status != "resolved") & (Incident.updated_at >= grenze),
            Incident.resolved_at >= grenze)))).scalars().all()

    def kanon(h: str) -> str:
        e, k, _grund = verz.zuordnen(h)
        return e.name if e else k

    erg = {"incidents": len(incs), "markiert": 0, "ohne_filter": [], "fehler": []}
    for inc in incs:
        f = filter_von(inc.kind, inc.primary_host, cluster_context(inc), kanon)
        if not f:
            erg["ohne_filter"].append({"id": str(inc.id), "kind": inc.kind})
            continue
        seit, bis = fenster(inc.created_at, inc.resolved_at, jetzt)
        try:
            erg["markiert"] += await markiere(str(inc.id), f, seit, bis)
        except Exception as e:  # noqa: BLE001 — ein Incident darf den Lauf nicht abbrechen
            erg["fehler"].append({"id": str(inc.id), "filter": f, "fehler": f"{type(e).__name__}: {str(e)[:160]}"})
    erg["ohne_filter"] = erg["ohne_filter"][:20]
    return erg


def ticket_von(ctx_liste: list | None) -> str | None:
    """Schlüssel des (letzten) Tickets an einem Incident, sonst None."""
    keys = [e.get("key") for e in ctx_liste or [] if isinstance(e, dict) and e.get("type") == "ticket" and e.get("key")]
    return keys[-1] if keys else None


async def behandelt(db, verz) -> list[tuple[dict, str]]:
    """Offene Incidents mit Ticket → (Bedingung ihres Filters, Grund) für das Raster (N2 „behandelt“:
    Plan — offenes Ticket = bekannt und behandelt). Vorher kannte N2 nur CheckMK-Quittung/Downtime."""
    from sqlalchemy import select

    from app.models.workflow import Incident
    from app.services.error_clusters.incident import cluster_context
    from app.services.log_watch import filter as flt

    def kanon(h: str) -> str:
        e, k, _grund = verz.zuordnen(h)
        return e.name if e else k

    out = []
    for inc in (await db.execute(select(Incident).where(Incident.status != "resolved"))).scalars():
        key = ticket_von(inc.causal_context)
        f = filter_von(inc.kind, inc.primary_host, cluster_context(inc), kanon) if key else None
        if f:
            out.append((flt.bedingung(f), f"Ticket {key} am Incident „{(inc.title or '')[:80]}“ (behandelt)"))
    return out


async def titel(ids: list[str]) -> dict[str, dict]:
    """Titel, Art und Status für Incident-Knoten — ein Objekt mit Namen, kein UUID."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Incident
    gueltig = []
    for i in ids:
        try:
            gueltig.append(_uuid.UUID(i))
        except ValueError:
            pass
    if not gueltig:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Incident.id, Incident.title, Incident.kind, Incident.status)
                                 .where(Incident.id.in_(gueltig)))).all()
    return {str(r.id): {"titel": r.title, "kind": r.kind, "status": r.status} for r in rows}
