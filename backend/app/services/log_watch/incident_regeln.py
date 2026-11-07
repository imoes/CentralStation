"""Incident-Regeln auswerten — Incidents nach der Logik des Regelwerks (Nutzer 07.11.).

Eine Incident-Regel (regelwerk.Regel mit aktion=incident) ist ein Filter der einen Filtersprache, eine
Gruppe (muster | dienst | host), ein Zeitfenster und Mindestwerte (Nachrichten, Hosts, Quellen). Je
Gruppe, die im Fenster die Mindestwerte erreicht, entsteht ein Kandidat: „diese Regel eröffnet einen
Incident für muster:x“. Gezählt wird über die Historie (cs-logs-*), dieselbe Suche wie überall.

Schattenbetrieb: Regeln im Modus `test` eröffnen nichts. Jeder Lauf hält fest, welche Kandidaten sie
hätten und welche Incidents die alten Schwellen (error_clusters) im selben Zeitraum eröffnet haben —
je Art: beide, nur neu, nur alt.

Umschalten (Schritt 4) ist eine Regel im Modus `aktiv` — eine neue Version der Definition, kein zweiter
Schalter: für deren Art (`incident_art`) eröffnet und meldet nur noch das Regelwerk
(`regeln_uebernehmen`), die alten Schwellen liefern weiter ihre Signale ans Raster, eröffnen aber nichts.
Ein Regel-Incident wird fortgeschrieben, solange sein Schlüssel Kandidat ist, und geschlossen, wenn er
ein volles Fenster lang unter den Mindestwerten bleibt — kein Limbo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.services.log_watch.regelwerk import AKTIV, INCIDENT, Regel

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
                 "quellen": (b.get("quellen") or {}).get("value", 0),
                 "fenster": (b.get("fenster") or {}).get("value", 0)}
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
                # verschiedene Fünf-Minuten-Fenster mit Treffern
                "fenster": {"cardinality": {"script": {"lang": "painless",
                    "source": "doc['zeit'].value.toEpochSecond() / 300"}}},
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


# ── Umschalten: aktive Regeln eröffnen und melden ───────────────────────────

_UEBERNOMMEN: dict = {"t": 0.0, "arten": frozenset()}


async def regeln_uebernehmen(art: str) -> bool:
    """Entscheidet für diese Incident-Art das Regelwerk (eine aktive Incident-Regel dieser Art)? Die alten
    Schwellen (error_clusters) fragen das vor dem Eröffnen und Melden. 60 s zwischengespeichert."""
    import time

    if time.monotonic() - _UEBERNOMMEN["t"] > 60:
        try:
            from app.core.database import AsyncSessionLocal
            from app.services.log_watch.definition import aktuelle
            async with AsyncSessionLocal() as db:
                d = await aktuelle(db)
            _UEBERNOMMEN["arten"] = frozenset(r.incident_art for r in d.regeln
                                              if r.aktion == INCIDENT and r.modus == AKTIV)
        except Exception as e:  # noqa: BLE001 — im Zweifel bleibt der alte Weg zuständig (nichts geht verloren)
            log.warning("Incident-Regeln: aktive Arten nicht lesbar: %s", e)
            _UEBERNOMMEN["arten"] = frozenset()
        _UEBERNOMMEN["t"] = time.monotonic()
    return art in _UEBERNOMMEN["arten"]


def lesbar(text: str) -> str:
    """Drain-Platzhalter (<NUM>, <TS>, <IP> …) aus einem Muster für Betreff/Titel tilgen. Rein."""
    import re
    t = re.sub(r"<[A-Z]{2,6}>", "…", text or "")
    t = re.sub(r"…(\s+…)+", "…", t)          # nur direkt aufeinanderfolgende Platzhalter
    return re.sub(r"\s+", " ", t).strip(" …")[:120]


def kontext(k: dict, regel: Regel, beispiele: list[str], titel_muster: str = "") -> dict:
    """Incident- und Mail-Kontext eines Kandidaten — so, dass incident_objekt.filter_von und
    schluessel_alt denselben Schlüssel wiederfinden. Rein — testbar."""
    art, schl = regel.incident_art, k["schluessel"]
    wert = schl.split(":", 1)[1] if ":" in schl else schl
    ctx = {"regel": regel.id, "regel_name": regel.name, "regel_filter": regel.filter, "schluessel": schl,
           "fenster_min": regel.fenster_min, "nachrichten": k["nachrichten"], "quellen": k["quellen"],
           "hosts": k.get("host_liste") or [], "examples": beispiele[:3],
           "reasons": [f"Regel {regel.id} „{regel.name}“: {regel.filter} — "
                       + ", ".join(f"{k[m]} {m} (mindestens {v})" for m, v in (regel.mindestens or {}).items())
                       + f" in {regel.fenster_min} min"],
           "containers": [{"name": h, "count": 0} for h in k.get("host_liste") or []],
           "entity_kind": "Host", "template": titel_muster or schl}
    if art == "log_cluster":
        ctx["template_key"] = wert
    elif art == "fleet":
        ctx["service"] = wert
    return ctx


def titel(regel: Regel, k: dict, titel_muster: str = "") -> str:
    was = lesbar(titel_muster) if titel_muster else k["schluessel"].split(":", 1)[-1]
    return f"{regel.name}: {was} · {k['hosts']} Hosts · {k['nachrichten']} Meldungen"[:300]


async def _beispiele(schluessel: str, regel: Regel, objekte: dict, jetzt: datetime) -> list[str]:
    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    from app.services.log_watch.historie import PRAEFIX
    f = f"{regel.filter} {incident_tag(schluessel)}"
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 3, "sort": [{"relevanz": "desc"}, {"zeit": "desc"}], "_source": ["text"],
        "query": {"bool": {"filter": [flt.opensearch(f, objekte), {"range": {"zeit": {
            "gte": (jetzt - timedelta(minutes=regel.fenster_min)).isoformat()}}}]}}})
    return [(h["_source"].get("text") or "")[:600] for h in r["hits"]["hits"]]


def incident_tag(schluessel: str) -> str:
    """Schlüssel → Filterausdruck (Werte mit Leerzeichen in Anführungszeichen)."""
    from app.services.log_watch.incident_objekt import _tag
    art, _, wert = schluessel.partition(":")
    return _tag(art, wert)


async def eroeffne(regel: Regel, k: dict, objekte: dict, jetzt: datetime) -> dict:
    """Aktiver Kandidat → Incident eröffnen oder fortschreiben (gleiche Art, gleicher Schlüssel, offen);
    nur ein NEUER Incident wird gemeldet. Rückgabe {incident, neu}."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.error_cluster import LogTemplate
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import OPEN, cluster_context, open_cluster_incident

    async with AsyncSessionLocal() as db:
        offen = (await db.execute(select(Incident).where(Incident.kind == regel.incident_art,
                                                         Incident.status.in_(OPEN)))).scalars().all()
        for inc in offen:
            c = cluster_context(inc) or {}
            if c.get("schluessel") == k["schluessel"] or k["schluessel"] in schluessel_alt(
                    inc.kind, inc.primary_host, c):
                inc.updated_at = jetzt
                await db.commit()
                return {"incident": str(inc.id), "neu": False}
        muster = ""
        if regel.gruppe == "muster":
            t = await db.get(LogTemplate, k["schluessel"].split(":", 1)[1])
            muster = t.template if t else ""
        ctx = kontext(k, regel, await _beispiele(k["schluessel"], regel, objekte, jetzt), muster)
        hosts = ctx["hosts"] or [k["schluessel"].split(":", 1)[1]]
        inc = await open_cluster_incident(
            db, kind=regel.incident_art, title=titel(regel, k, muster),
            severity="high" if k["nachrichten"] >= 2 * max((regel.mindestens or {}).values() or [1]) else "medium",
            primary_host=hosts[0], context={**ctx, "hosts": hosts}, member_docs=[])
        await db.commit()
        return {"incident": str(inc.id), "neu": True, "ctx": {**ctx, "hosts": hosts}, "titel": inc.title}


async def melde(incident_id: str, ctx: dict, titel_text: str) -> None:
    """Derselbe Zustellweg wie bisher (notify.deliver: KI-Einschätzung, Empfänger nach Host-Scope, Rezept) —
    ein Mail-Weg; cluster_id ist hier der Incident (keine Fehler-Cluster-Episode)."""
    from app.core.database import AsyncSessionLocal
    from app.services.error_clusters import notify
    from app.services.settings import get_error_cluster_config
    async with AsyncSessionLocal() as db:
        cfg = await get_error_cluster_config(db)
    await notify.deliver(ctx, titel_text, incident_id, incident_id, cfg)


async def schliesse_ruhige(regeln: list[Regel], kandidaten: list[dict], jetzt: datetime) -> int:
    """Regel-Incidents schließen, deren Schlüssel ein volles Fenster lang kein Kandidat war."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import OPEN, close_cluster_incident, cluster_context
    aktiv = {r.id: r for r in regeln if r.modus == AKTIV}
    lebend = {(k["regel"], k["schluessel"]) for k in kandidaten}
    n = 0
    async with AsyncSessionLocal() as db:
        for inc in (await db.execute(select(Incident).where(Incident.status.in_(OPEN)))).scalars().all():
            c = cluster_context(inc) or {}
            r = aktiv.get(c.get("regel"))
            if r is None or (r.id, c.get("schluessel")) in lebend:
                continue
            if inc.updated_at < jetzt - timedelta(minutes=r.fenster_min):
                await close_cluster_incident(db, str(inc.id),
                                             f"Regel {r.id}: {r.fenster_min} min unter den Mindestwerten")
                n += 1
        await db.commit()
    return n


def umgeschaltet(regel_daten: list[dict], arten: set[str]) -> list[dict]:
    """Regeldaten mit den Incident-Regeln dieser Arten im Modus aktiv (alle anderen unverändert). Rein."""
    return [{**r, "modus": AKTIV} if r.get("aktion") == INCIDENT and r.get("incident_art") in arten else dict(r)
            for r in regel_daten]


async def schalte(arten: set[str], grund: str, wer: str) -> int:
    """Umschalten als neue Version der Definition (kein zweiter Schalter): die Incident-Regeln der Arten
    werden aktiv. Urheber ist der Mensch, der es entschieden hat — so überschreibt ein späterer Code-Standard
    die Entscheidung nicht still (definition.uebernahme: admin_aelter bleibt, benannt). Rückgabe: Version."""
    from app.core.database import AsyncSessionLocal
    from app.models.audit import AuditLog
    from app.models.log_watch import RelevanzDefinition
    from app.services.log_watch.definition import aktuelle
    from app.services.log_watch.regelwerk import als_daten, pruefe

    if not grund.strip() or not wer.strip():
        raise ValueError("Grund und Urheber sind Pflicht")
    async with AsyncSessionLocal() as db:
        d = await aktuelle(db)
        daten = umgeschaltet(als_daten(d.regeln), arten)
        fehler = pruefe(daten, {k["id"] for k in d.kriterien}, d.objekte)
        if fehler:
            raise ValueError("; ".join(fehler))
        row = RelevanzDefinition(version=d.version + 1, kriterien=d.kriterien, parameter=d.parameter,
                                 regeln=daten, objekte=d.objekte, begruendung=grund[:1000], erstellt_von=wer,
                                 erstellt_at=datetime.now(timezone.utc))
        db.add(row)
        db.add(AuditLog(action="log_relevanz_definition", resource_type="relevanz_definition",
                        resource_id=str(row.version), user_id=None,
                        new_value={"version": row.version, "begruendung": grund, "von": wer,
                                   "incident_regeln_aktiv": sorted(arten)}))
        await db.commit()
    _UEBERNOMMEN["t"] = 0.0                    # Weiche sofort neu lesen
    return row.version


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
    # Umschalten: aktive Regeln eröffnen/fortschreiben und melden; Ruhige schließen
    aktiv = {r.id: r for r in regeln if r.modus == AKTIV}
    eroeffnet, fortgeschrieben, gemeldet, fehler = 0, 0, 0, []
    for k in kand:
        r = aktiv.get(k["regel"])
        if r is None:
            continue
        try:
            e = await eroeffne(r, k, d.objekte, jetzt)
            if e["neu"]:
                eroeffnet += 1
                from app.core.tasks import run_background
                run_background(melde(e["incident"], e["ctx"], e["titel"]), name=f"incident_regel_{e['incident'][:8]}")
                gemeldet += 1
            else:
                fortgeschrieben += 1
        except Exception as ex:  # noqa: BLE001 — ein Kandidat darf den Lauf nicht abbrechen
            fehler.append({"schluessel": k["schluessel"], "fehler": f"{type(ex).__name__}: {str(ex)[:160]}"})
    geschlossen = await schliesse_ruhige(regeln, kand, jetzt) if aktiv else 0
    return {"definition_v": d.version,
            "aktiv": {"eroeffnet": eroeffnet, "fortgeschrieben": fortgeschrieben, "gemeldet": gemeldet,
                      "geschlossen": geschlossen, "fehler": fehler[:10]}, "regeln": [{"id": r.id, "modus": r.modus, "fenster_min": r.fenster_min}
                                                  for r in regeln],
            "kandidaten": len(kand), "je_regel": {r.id: sum(1 for k in kand if k["regel"] == r.id) for r in regeln},
            "vergleich": vergleiche(kand, alt), "beispiele": kand[:20]}
