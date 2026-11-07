"""Incident-Regeln auswerten — Incidents nach der Logik des Regelwerks (Nutzer 07.11.).

Eine Incident-Regel (regelwerk.Regel mit aktion=incident) ist ein Filter der einen Filtersprache, eine
Gruppe (muster | dienst | host), ein Zeitfenster und Mindestwerte (Nachrichten, Hosts, Quellen). Je
Gruppe, die im Fenster die Mindestwerte erreicht, entsteht ein Kandidat: „diese Regel eröffnet einen
Incident für muster:x“. Gezählt wird über die Historie (cs-logs-*), dieselbe Suche wie überall.

Schattenbetrieb: Regeln im Modus `test` eröffnen nichts. Jeder Lauf hält fest, welche Kandidaten sie
hätten und welche Incidents die alten Schwellen (error_clusters) im selben Zeitraum eröffnet haben —
je Art: beide, nur neu, nur alt. Erst mit diesem Vergleich wird umgeschaltet (Schritt 4).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.services.log_watch.regelwerk import INCIDENT, Regel

log = logging.getLogger(__name__)

#: Gruppe → (Aggregation über das Feld, Präfix des Schlüssels)
_GRUPPE = {
    "muster": ({"field": "hashtags", "include": "muster:.*"}, ""),
    "dienst": ({"field": "hashtags", "include": "dienst:.*"}, ""),
    "host": ({"field": "host"}, "host:"),
}
GRUPPEN_MAX = 500


def kandidaten_aus(regel: Regel, buckets: list[dict]) -> list[dict]:
    """terms-Buckets einer Regel → Kandidaten, die die Mindestwerte erreichen. Rein — testbar."""
    _, praefix = _GRUPPE[regel.gruppe]
    mi = regel.mindestens or {}
    out = []
    for b in buckets:
        werte = {"nachrichten": b["doc_count"], "hosts": (b.get("hosts") or {}).get("value", 0),
                 "quellen": (b.get("quellen") or {}).get("value", 0)}
        if all(werte[k] >= v for k, v in mi.items()):
            out.append({"regel": regel.id, "art": regel.incident_art, "schluessel": praefix + str(b["key"]),
                        **werte, "host_liste": [h["key"] for h in (b.get("host_liste") or {}).get("buckets", [])]})
    return out


async def auswerten(regeln: list[Regel], objekte: dict, jetzt: datetime) -> list[dict]:
    """Kandidaten aller Incident-Regeln (aktiv und test) zum Zeitpunkt `jetzt`."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    from app.services.log_watch.historie import PRAEFIX
    os_client = get_opensearch()
    out: list[dict] = []
    for r in regeln:
        if r.aktion != INCIDENT:
            continue
        terms, _ = _GRUPPE[r.gruppe]
        res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
            "size": 0,
            "query": {"bool": {"filter": [flt.opensearch(r.filter, objekte),
                                          {"range": {"zeit": {"gte": (jetzt - timedelta(minutes=r.fenster_min)).isoformat(),
                                                              "lte": jetzt.isoformat()}}}],
                               "must_not": [{"terms": {"quelle": ["ki", "konsole"]}}]}},
            "aggs": {"g": {"terms": {**terms, "size": GRUPPEN_MAX}, "aggs": {
                "hosts": {"cardinality": {"field": "host"}}, "quellen": {"cardinality": {"field": "quelle"}},
                "host_liste": {"terms": {"field": "host", "size": 10}}}}}})
        k = kandidaten_aus(r, res["aggregations"]["g"]["buckets"])
        for x in k:
            x["modus"] = r.modus
        out += k
    return out


def schluessel_alt(kind: str, primary_host: str, ctx: dict | None, kanon=lambda h: h) -> list[str]:
    """Schlüssel eines Incidents der alten Schwellen, in derselben Form wie die Kandidaten. Rein."""
    ctx = ctx or {}
    if kind == "log_cluster":
        keys = [ctx.get("template_key")] + [p.get("template_key") for p in ctx.get("patterns") or []
                                            if isinstance(p, dict)]
        return [f"muster:{k}" for k in keys if k]
    if kind == "fleet":
        return [f"dienst:{ctx['service']}"] if ctx.get("service") else []
    if kind in ("critical", "host") and primary_host:
        return [f"host:{kanon(primary_host)}"]
    return []


def vergleiche(kandidaten: list[dict], alt: dict[str, set[str]]) -> dict:
    """Je Art: beide, nur neu (Regel hätte eröffnet, alte Schwellen nicht), nur alt. Rein — testbar."""
    neu: dict[str, set[str]] = {}
    for k in kandidaten:
        neu.setdefault(k["art"], set()).add(k["schluessel"])
    out = {}
    for art in sorted(set(neu) | set(alt)):
        n, a = neu.get(art, set()), alt.get(art, set())
        out[art] = {"beide": len(n & a), "nur_neu": len(n - a), "nur_alt": len(a - n),
                    "nur_neu_beispiele": sorted(n - a)[:10], "nur_alt_beispiele": sorted(a - n)[:10]}
    return out


async def lauf(jetzt: datetime | None = None) -> dict:
    """Ein Messpunkt des Schattenbetriebs (raster_lauf „incident-regeln“)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import cluster_context
    from app.services.log_watch.definition import aktuelle
    from app.services.log_watch.hosts import verzeichnis

    jetzt = jetzt or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        d = await aktuelle(db)
        verz = await verzeichnis(db)
        regeln = [r for r in d.regeln if r.aktion == INCIDENT]
        # je Art das Fenster ihrer Regel — sonst zählte „alt“ über 6 h, „neu“ über 30 min (gemessen 07.11.:
        # 11 bzw. 46 „nur alt“ allein aus dem Fensterunterschied)
        fenster_je_art = {r.incident_art: r.fenster_min for r in regeln}
        fenster = max(fenster_je_art.values() or [30])
        incs = (await db.execute(select(Incident).where(
            Incident.updated_at >= jetzt - timedelta(minutes=fenster)))).scalars().all()

    def kanon(h: str) -> str:
        e, k, _grund = verz.zuordnen(h)
        return e.name if e else k

    alt: dict[str, set[str]] = {}
    for inc in incs:
        grenze = jetzt - timedelta(minutes=fenster_je_art.get(inc.kind, fenster))
        if inc.updated_at < grenze or (inc.status == "resolved" and inc.resolved_at and inc.resolved_at < grenze):
            continue
        alt.setdefault(inc.kind, set()).update(schluessel_alt(inc.kind, inc.primary_host, cluster_context(inc), kanon))
    kand = await auswerten(regeln, d.objekte, jetzt)
    return {"definition_v": d.version, "regeln": [{"id": r.id, "modus": r.modus, "fenster_min": r.fenster_min}
                                                  for r in regeln],
            "kandidaten": len(kand), "je_regel": {r.id: sum(1 for k in kand if k["regel"] == r.id) for r in regeln},
            "vergleich": vergleiche(kand, alt), "beispiele": kand[:20]}
