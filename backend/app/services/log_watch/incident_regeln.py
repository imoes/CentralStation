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
                        **werte, "host_liste": [h["key"] for h in (b.get("host_liste") or {}).get("buckets", [])],
                        "erste": (b.get("erste") or {}).get("value_as_string"),
                        "letzte": (b.get("letzte") or {}).get("value_as_string")})
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
                "erste": {"min": {"field": "zeit"}}, "letzte": {"max": {"field": "zeit"}},
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


#: Bremsen aus dem Backtest der Fehler-Cluster (Projektnotiz, „nicht zurückdrehen“) — gelten auch hier
#: wieder aufflammend = derselbe Incident (Nutzer 09.11.: „Wiedereröffnen statt neu“; vorher 3 h — gemessen kam
#: cue0111 I-HOST nach 3 h 05 min wieder und wurde ein neuer Incident mit Mail). Mail entscheidet bearbeitung.py.
from app.services.error_clusters.bearbeitung import WIEDER_FENSTER as COOLDOWN  # noqa: E402
GRUPPE_FENSTER = timedelta(minutes=30)   # gleichzeitig mit geteilten Hosts = ein Incident
GRUPPE_JACCARD = 0.2
HOST_MINDESTALTER = timedelta(minutes=15)
NOCH_AKTIV = timedelta(minutes=15)


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


async def freigabe(regel: Regel, k: dict, jetzt: datetime) -> str | None:
    """Darf dieser Kandidat einen Incident eröffnen? None = ja, sonst der Grund. Die Statistik der
    Fehler-Cluster bleibt die Messung (Grundlast, Median, Mindestalter, Wiederkehr, stumm) — die Regel
    entscheidet; so gilt alles, was der Backtest belegt hat, ohne zweite Umsetzung:
      I-AUSBRUCH  Episode des Musters ≥ min_age_minutes alt und noch aktiv (wie resend_pending)
      I-FLOTTE    offene Flotten-Episode, nicht wiederkehrend (Orderserver jede Nacht = kein Incident)
      I-HOST      erste dringende Meldung ≥ 15 min alt, letzte ≤ 15 min (Neustarts mailen nicht)"""
    wert = k["schluessel"].split(":", 1)[1]
    if regel.incident_art == "host":
        if k.get("erste") and k["erste"] > (jetzt - HOST_MINDESTALTER).isoformat():
            return "erste dringende Meldung jünger als 15 min"
        if k.get("letzte") and k["letzte"] < (jetzt - NOCH_AKTIV).isoformat():
            return "nicht mehr aktiv"
        return None
    if regel.incident_art not in ("log_cluster", "fleet"):
        return None
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.error_cluster import ErrorCluster, LogTemplate
    from app.services.error_clusters.fleet import fleet_key
    from app.services.error_clusters.rules import DEFAULT
    if regel.incident_art == "log_cluster":
        async with AsyncSessionLocal() as db:
            ep = (await db.execute(select(ErrorCluster).where(
                ErrorCluster.source == "log", ErrorCluster.key == wert, ErrorCluster.status == "open")
                .order_by(ErrorCluster.started_at.desc()).limit(1))).scalar_one_or_none()
            stumm = (await db.execute(select(LogTemplate.muted).where(LogTemplate.key == wert))).scalar()
        if stumm:
            return "Muster stumm geschaltet"
        if ep is None:
            return "keine offene Episode des Musters"
        if ep.started_at > jetzt - timedelta(minutes=DEFAULT.min_age_minutes):
            return f"Ausbruch jünger als {DEFAULT.min_age_minutes} min (Mindestalter)"
        if ep.last_active_at < jetzt - NOCH_AKTIV:
            return "Ausbruch nicht mehr aktiv"
        return None
    if regel.incident_art == "fleet":
        key = fleet_key(wert)
        async with AsyncSessionLocal() as db:
            ep = (await db.execute(select(ErrorCluster).where(
                ErrorCluster.source == "checkmk_fleet", ErrorCluster.key == key, ErrorCluster.status == "open")
                .order_by(ErrorCluster.started_at.desc()).limit(1))).scalar_one_or_none()
            stumm = (await db.execute(select(LogTemplate.muted).where(LogTemplate.key == key))).scalar()
        if stumm:
            return "Dienst stumm geschaltet"
        if ep is None:
            return "kein Flotten-Ausfall laut Statistik (Mindestalter 30–45 min, ≥ 3× Median)"
        if "recurring" in (ep.rules or []):
            return "wiederkehrender Lauf (gleiche Uhrzeit an ≥ 3 von 7 Tagen) — nur Tagesübersicht"
        return None
    return None


def schwere(regel: Regel, k: dict) -> str:
    """Schwere eines Kandidaten — eine Quelle für Eröffnen und Eskalationsprüfung. Rein."""
    return "high" if k["nachrichten"] >= 2 * max((regel.mindestens or {}).values() or [1]) else "medium"


async def eroeffne(regel: Regel, k: dict, objekte: dict, jetzt: datetime) -> dict:
    """Aktiver Kandidat → Incident eröffnen oder fortschreiben (gleiche Art, gleicher Schlüssel, offen);
    nur ein NEUER Incident wird gemeldet. Rückgabe {incident, neu}."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.error_cluster import LogTemplate
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import OPEN, cluster_context, open_cluster_incident

    async with AsyncSessionLocal() as db:
        from app.services.error_clusters import bearbeitung as bb
        from app.services.error_clusters.incident import set_cluster_context
        neue_hosts = list(k.get("host_liste") or [])
        neue_schwere = schwere(regel, k)

        def _fortschreiben(inc, c: dict, wieder: bool) -> dict:
            """Bekannten Incident fortschreiben: Hosts ergänzen, Wiederauftreten vermerken, Eskalation prüfen."""
            esk = bb.eskalation(c, neue_hosts, inc.severity, neue_schwere)
            c2 = {kk: v for kk, v in c.items() if kk != "type"}
            if neue_hosts:
                c2["hosts"] = sorted(set(c2.get("hosts") or []) | set(neue_hosts))
            if wieder:
                c2 = bb.wieder_vermerk(c2, jetzt)
                inc.status, inc.resolved_at = "open", None
            if esk and bb.SCHWERE.get(neue_schwere, 0) > bb.SCHWERE.get(inc.severity, 0):
                inc.severity = neue_schwere
            set_cluster_context(inc, c2)
            inc.updated_at = jetzt
            return {"incident": str(inc.id), "neu": False, "wieder": wieder, "eskalation": esk,
                    "ctx": {**c2, "wieder_anzahl": bb.anzahl_24h(c2, jetzt), "eskalation": esk},
                    "titel": inc.title, "causal_context": list(inc.causal_context or [])}

        offen = (await db.execute(select(Incident).where(Incident.kind == regel.incident_art,
                                                         Incident.status.in_(OPEN)))).scalars().all()
        for inc in offen:
            c = cluster_context(inc) or {}
            if c.get("schluessel") == k["schluessel"] or k["schluessel"] in schluessel_alt(
                    inc.kind, inc.primary_host, c):
                e = _fortschreiben(inc, c, wieder=False)
                await db.commit()
                return e
        # In den letzten 24 h geschlossen, gleicher Schlüssel → derselbe Incident lebt wieder auf
        zu = (await db.execute(select(Incident).where(
            Incident.kind == regel.incident_art, Incident.status == "resolved",
            Incident.resolved_at >= jetzt - COOLDOWN))).scalars().all()
        for inc in zu:
            c = cluster_context(inc) or {}
            if c.get("schluessel") == k["schluessel"]:
                e = _fortschreiben(inc, c, wieder=True)
                await db.commit()
                return e
        # Gleiche Regel, gemeinsamer Host, offen oder in den letzten 24 h geschlossen → anhängen statt neu
        # (Nutzer 09.11.: „gleicher Host + gleiche Regel“; gemessen cue0113: 6 Muster = 6 Incidents an einem Tag)
        if neue_hosts:
            nh = {h.lower() for h in neue_hosts}
            for inc in list(offen) + list(zu):
                c = cluster_context(inc) or {}
                if c.get("regel") == regel.id and nh & {h.lower() for h in c.get("hosts") or []}:
                    c = {**c, "weitere_schluessel": sorted(set(c.get("weitere_schluessel") or []) | {k["schluessel"]})}
                    e = _fortschreiben(inc, c, wieder=inc.status == "resolved")
                    e["angehaengt"] = k["schluessel"]
                    await db.commit()
                    return e
        # Gruppierung: gleichzeitiger Ausbruch mit geteilten Hosts = ein Incident (fleet.group_leader)
        if regel.incident_art in ("log_cluster", "fleet"):
            hosts = set(k.get("host_liste") or [])
            for inc in offen:
                c = cluster_context(inc) or {}
                if (c.get("regel") == regel.id and inc.created_at >= jetzt - GRUPPE_FENSTER
                        and jaccard(hosts, set(c.get("hosts") or [])) >= GRUPPE_JACCARD):
                    c2 = {kk: v for kk, v in c.items() if kk != "type"}
                    c2["weitere_schluessel"] = sorted(set(c2.get("weitere_schluessel") or []) | {k["schluessel"]})
                    from app.services.error_clusters.incident import set_cluster_context
                    set_cluster_context(inc, c2)
                    inc.updated_at = jetzt
                    await db.commit()
                    return {"incident": str(inc.id), "neu": False, "gruppiert": True}
        muster = ""
        if regel.gruppe == "muster":
            t = await db.get(LogTemplate, k["schluessel"].split(":", 1)[1])
            muster = t.template if t else ""
        ctx = kontext(k, regel, await _beispiele(k["schluessel"], regel, objekte, jetzt), muster)
        hosts = ctx["hosts"] or [k["schluessel"].split(":", 1)[1]]
        inc = await open_cluster_incident(
            db, kind=regel.incident_art, title=titel(regel, k, muster),
            severity=schwere(regel, k),
            primary_host=hosts[0], context={**ctx, "hosts": hosts}, member_docs=[])
        await db.commit()
        return {"incident": str(inc.id), "neu": True, "ctx": {**ctx, "hosts": hosts}, "titel": inc.title}


#: Meldungen, die dieser Prozess gerade zustellt — nach einem Neustart leer; dann holt `nachzuholen` sie nach.
#: Gemessen 07.11.: zwei I-HOST-Incidents von 16:09 blieben ohne Zustellvermerk, der Neustart um 16:19 brach
#: die Meldung (Triage durch das Modell) ab — vorher ging so eine Mail still verloren.
_IN_ARBEIT: set[str] = set()
NACHHOLEN_STUNDEN = 2
NACHHOLEN_AB_MIN = 10          # einer laufenden Meldung (Triage braucht Minuten) Zeit lassen


def nachzuholen(incidents: list[tuple[str, dict, datetime]], in_arbeit: set[str], jetzt: datetime) -> list[str]:
    """(id, Kontext, eröffnet) offener Regel-Incidents → IDs, deren Meldung fehlt: kein Zustellvermerk, nicht in
    Arbeit, älter als NACHHOLEN_AB_MIN, jünger als NACHHOLEN_STUNDEN. Rein — testbar."""
    return [i for i, c, t in incidents
            if c.get("regel") and "zustellungen" not in c and i not in in_arbeit
            and timedelta(minutes=NACHHOLEN_AB_MIN) <= jetzt - t <= timedelta(hours=NACHHOLEN_STUNDEN)]


async def melde(incident_id: str, ctx: dict, titel_text: str) -> None:
    """Derselbe Zustellweg wie bisher (notify.deliver: KI-Einschätzung, Empfänger nach Host-Scope, Rezept) —
    ein Mail-Weg; cluster_id ist hier der Incident (keine Fehler-Cluster-Episode)."""
    from app.core.database import AsyncSessionLocal
    from app.services.error_clusters import notify
    from app.services.settings import get_error_cluster_config
    async with AsyncSessionLocal() as db:
        cfg = await get_error_cluster_config(db)
    _IN_ARBEIT.add(incident_id)
    try:
        await notify.deliver(ctx, titel_text, incident_id, incident_id, cfg)
    finally:
        _IN_ARBEIT.discard(incident_id)


async def _hole_meldungen_nach(jetzt: datetime) -> list[str]:
    """Meldungen offener Regel-Incidents ohne Zustellvermerk erneut anstoßen (nach Neustart abgebrochen)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.tasks import run_background
    from app.models.workflow import Incident
    from app.services.error_clusters.incident import OPEN, cluster_context
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Incident).where(
            Incident.status.in_(OPEN), Incident.created_at >= jetzt - timedelta(hours=NACHHOLEN_STUNDEN)))).scalars().all()
        kandidaten = [(str(i.id), cluster_context(i) or {}, i.created_at, i.title) for i in rows]
    ids = nachzuholen([(i, c, t) for i, c, t, _ in kandidaten], _IN_ARBEIT, jetzt)
    for i, c, _t, titel_text in kandidaten:
        if i in ids:
            ctx = {k: v for k, v in c.items() if k != "type"}
            run_background(melde(i, ctx, titel_text), name=f"incident_regel_nachholen_{i[:8]}")
    return ids


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
            Incident.created_at >= jetzt - timedelta(minutes=fenster)))).scalars().all()

    def kanon(h: str) -> str:
        e, k, _grund = verz.zuordnen(h)
        return e.name if e else k

    alt: dict[str, set[str]] = {}
    for inc in incs:
        # verglichen werden ERÖFFNUNGEN im Fenster der Regel — gemessen 07.11.: mit `updated_at` zählten
        # Incidents von 09:13 und vom Vortag als „nur alt“, weil ihr Zeitstempel später berührt wurde
        grenze = jetzt - timedelta(minutes=fenster_je_art.get(inc.kind, fenster))
        if inc.created_at < grenze:
            continue
        alt.setdefault(inc.kind, set()).update(schluessel_alt(inc.kind, inc.primary_host, cluster_context(inc), kanon))
    kand = await auswerten(regeln, d.objekte, jetzt)
    # Umschalten: aktive Regeln eröffnen/fortschreiben und melden; Ruhige schließen
    aktiv = {r.id: r for r in regeln if r.modus == AKTIV}
    eroeffnet, fortgeschrieben, gemeldet, fehler, zurueckgehalten, still = 0, 0, 0, [], [], []
    for k in kand:
        r = aktiv.get(k["regel"])
        if r is None:
            continue
        try:
            grund = await freigabe(r, k, jetzt)
            if grund:
                zurueckgehalten.append({"schluessel": k["schluessel"], "grund": grund})
                continue
            e = await eroeffne(r, k, d.objekte, jetzt)
            from app.core.tasks import run_background
            if e["neu"]:
                eroeffnet += 1
                run_background(melde(e["incident"], e["ctx"], e["titel"]), name=f"incident_regel_{e['incident'][:8]}")
                gemeldet += 1
            else:
                fortgeschrieben += 1
                anlass = "eskalation" if e.get("eskalation") else ("wieder" if e.get("wieder") else None)
                if anlass:
                    from app.services.error_clusters.bearbeitung import mail_erlaubt
                    ok, warum = mail_erlaubt(anlass, e.get("causal_context"), e.get("ctx"), jetzt)
                    if ok:
                        run_background(melde(e["incident"], {**e["ctx"], "mail_anlass": anlass}, e["titel"]),
                                       name=f"incident_regel_wieder_{e['incident'][:8]}")
                        gemeldet += 1
                    else:
                        still.append({"incident": e["incident"], "anlass": anlass, "grund": warum})
        except Exception as ex:  # noqa: BLE001 — ein Kandidat darf den Lauf nicht abbrechen
            fehler.append({"schluessel": k["schluessel"], "fehler": f"{type(ex).__name__}: {str(ex)[:160]}"})
    nachgeholt = await _hole_meldungen_nach(jetzt) if aktiv else []
    geschlossen = await schliesse_ruhige(regeln, kand, jetzt) if aktiv else 0
    return {"definition_v": d.version,
            "aktiv": {"eroeffnet": eroeffnet, "fortgeschrieben": fortgeschrieben, "gemeldet": gemeldet,
                      "still": still,
                      "nachgeholt": nachgeholt,
                      "geschlossen": geschlossen, "fehler": fehler[:10],
                      # nicht eröffnet, mit Grund (Mindestalter, Wiederkehr, stumm …) — nichts still
                      "zurueckgehalten": zurueckgehalten[:20]}, "regeln": [{"id": r.id, "modus": r.modus, "fenster_min": r.fenster_min}
                                                  for r in regeln],
            "kandidaten": len(kand), "je_regel": {r.id: sum(1 for k in kand if k["regel"] == r.id) for r in regeln},
            "vergleich": vergleiche(kand, alt), "beispiele": kand[:20]}
