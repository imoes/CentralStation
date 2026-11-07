"""Raster im Betrieb — zunächst als TROCKENLAUF: es wird bewertet und gezählt, nichts
gespeichert. Aus den Zahlen (Tabelle `raster_lauf`) folgt, wie viel `cs-logs-*` pro Tag
aufnehmen würde, bevor dort etwas geschrieben wird.

Graylog wird im 5-min-Durchlauf der Fehler-Cluster-Erkennung bewertet (die Zeilen sind
dort schon geholt und Mustern zugeordnet — kein zweiter Abruf). Wazuh und CheckMK
bewertet `run_raster` im eigenen 5-min-Takt.
"""
from __future__ import annotations

import copy
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

from app.services.log_watch.definition import RELEVANT
from app.services.log_watch.raster import (
    KONTINGENT, Kontingent, Treffer, bewerte_checkmk, bewerte_graylog, bewerte_wazuh,
    quellenuebergreifend, wazuh_zeilen, zaehle,
)

log = logging.getLogger(__name__)

FENSTER = timedelta(minutes=5)
#: Wazuh-Alerts brauchen etwas, bis sie im Indexer liegen.
WAZUH_VERZUG = timedelta(minutes=3)
#: Zeitraum, in dem ein Host in zwei Quellen „zugleich“ relevant ist.
UEBERGREIFEND = timedelta(minutes=15)

_kontingent = Kontingent()
_checkmk_vorher: dict[str, str] | None = None
#: (Fenster, Treffer) der letzten UEBERGREIFEND, alle Quellen
_juengst: deque[tuple[datetime, Treffer]] = deque()


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _boden(ts: datetime) -> datetime:
    ts = ts.replace(second=0, microsecond=0)
    return ts - timedelta(minutes=ts.minute % 5)


def _stunde(b: datetime) -> datetime:
    return b.replace(minute=0, second=0, microsecond=0)


def _merke(b: datetime, treffer: list[Treffer]) -> None:
    for t in treffer:
        _juengst.append((b, t))
    grenze = _jetzt() - UEBERGREIFEND - FENSTER
    while _juengst and _juengst[0][0] < grenze:
        _juengst.popleft()


async def _speichere(quelle: str, b: datetime, werte: dict) -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import RasterLauf

    async with AsyncSessionLocal() as db:
        await db.merge(RasterLauf(fenster_start=b, quelle=quelle, werte=werte,
                                  created_at=_jetzt()))
        await db.commit()


async def _grundlagen():
    """Hostverzeichnis und aktuelle Relevanz-Definition — beides braucht jedes Urteil."""
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.definition import aktuelle
    from app.services.log_watch.hosts import verzeichnis

    from app.services.log_watch import incident_objekt, raster

    async with AsyncSessionLocal() as db:
        verz, d = await verzeichnis(db), await aktuelle(db)
        # Ist-Zustand „behandelt“ aus Incidents mit Ticket — je Lauf neu, kein Teil der Definition
        try:
            raster.setze_behandelt(await incident_objekt.behandelt(db, verz))
        except Exception as e:  # noqa: BLE001 — ohne diese Liste urteilt das Raster wie bisher
            log.warning("Behandelt-Liste aus Incidents nicht geladen: %s", e)
            raster.setze_behandelt([])
        return verz, d


#: Ein gespeicherter CheckMK-Zustand gilt nach einem Neustart nur so lange als Vergleich.
ZUSTAND_GUELTIG = timedelta(hours=2)


def gueltiger_zustand(werte: dict | None, alter: timedelta) -> dict[str, str] | None:
    """Gespeicherten Zustand übernehmen, wenn er jung genug ist — sonst erster Abgleich."""
    if not werte or alter > ZUSTAND_GUELTIG or not isinstance(werte.get("zustand"), dict):
        return None
    return dict(werte["zustand"])


async def _letzter_checkmk_zustand(jetzt: datetime) -> dict[str, str] | None:
    """Nach einem Neustart: Zustand des letzten Abgleichs aus `raster_lauf` — sonst gingen
    Änderungen während des Neustarts still verloren."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import RasterLauf

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(RasterLauf).where(RasterLauf.quelle == "checkmk")
                                .order_by(RasterLauf.fenster_start.desc()).limit(1))).scalar_one_or_none()
    return gueltiger_zustand(row.werte, jetzt - row.created_at) if row else None


# ── Historie: was relevant ist, mit allen Originalfeldern speichern ────────────

async def _bewahre(quelle: str, treffer: list[Treffer], holer) -> dict | None:
    """Relevante Treffer in die Historie schreiben — genau `kontingent_n` Nachrichten je Treffer
    (dieselbe Entscheidung wie in der Zählung). `holer(paare)` liefert zu [(Treffer, n)] die
    vollständigen Nachrichten [(Treffer, quell_id, zeit, text, roh)]. Schalter aus: None."""
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch import historie

    async with AsyncSessionLocal() as db:
        if not await historie.aktiv(db):
            return None
    paare = [(t, t.kontingent_n) for t in treffer if t.kontingent_n > 0]
    if not paare:
        return {"geschrieben": 0, "fehler": 0, "vektor_fehlt": 0, "angefordert": 0, "gefunden": 0}
    try:
        nachrichten = await holer(paare)
        docs = []
        for t, qid, zeit, text, roh in nachrichten:
            doc_id, doc = historie.dokument(t, qid, zeit, text, roh)
            docs.append((doc_id, zeit, doc))
        erg = await historie.schreibe(docs)
        erg.update(angefordert=sum(n for _, n in paare), gefunden=len(nachrichten))
        return erg
    except Exception as e:  # noqa: BLE001 — Raster und Zählung laufen weiter
        log.warning("Historie %s: Schreiben fehlgeschlagen: %s", quelle, e)
        return {"fehler_text": f"{type(e).__name__}: {str(e)[:300]}"}


def _zeit(s, ersatz: datetime) -> datetime:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ersatz


def _graylog_holer(gl, b: datetime):
    """Graylog-Nachrichten über ihre IDs nachholen (alle Felder), in Stücken zu 50 IDs."""
    from app.services.graylog_occurrences import iso

    async def hole(paare):
        gesucht = [(t, i) for t, n in paare for i, _ts in (t.belege or [])[:n]]
        gefunden: dict[str, dict] = {}
        ids = [i for _, i in gesucht]
        for k in range(0, len(ids), 50):
            teil = ids[k:k + 50]
            msgs, _ = await gl.search_window("_id:(" + " OR ".join(f'"{i}"' for i in teil) + ")",
                                             iso(b - timedelta(minutes=10)), iso(b + timedelta(minutes=15)),
                                             limit=len(teil))
            gefunden.update({m.get("_id"): m for m in msgs})
        out = []
        for t, i in gesucht:
            m = gefunden.get(i)
            if m is None:
                continue
            roh = {k: v for k, v in m.items() if not k.startswith("gl2_") and k != "streams"}
            out.append((t, i, _zeit(m.get("timestamp"), b), str(m.get("message") or t.beispiel), roh))
        return out
    return hole


def _wazuh_holer(w, b: datetime):
    """Wazuh-Alerts über die IDs, die die Aggregation je (Regel × Agent) mitgeliefert hat."""
    async def hole(paare):
        gesucht = [(t, i) for t, n in paare for i, _idx in (t.belege or [])[:n]]
        if not gesucht:
            return []
        body = {"size": len(gesucht), "query": {"ids": {"values": [i for _, i in gesucht]}}}
        async with w._client(timeout=120.0) as client:
            res = await w._query_indexer(client, "wazuh-alerts-*", body)
        gefunden = {h["_id"]: h.get("_source") or {} for h in (res.get("hits") or {}).get("hits") or []}
        out = []
        for t, i in gesucht:
            src = gefunden.get(i)
            if src is None:
                continue
            text = src.get("full_log") or (src.get("rule") or {}).get("description") or ""
            out.append((t, i, _zeit(src.get("timestamp"), b), text, src))
        return out
    return hole


def _checkmk_holer(probleme: list[dict], bc: datetime):
    """CheckMK: das Problem selbst ist die Nachricht; ID = Host|Service|Zustandswechsel."""
    je = {(p["host"], p["service"]): p for p in probleme}

    async def hole(paare):
        out = []
        for t, _n in paare:
            dienst = t.schluessel.split(":", 1)[1]
            p = next((v for (h, s), v in je.items() if s == dienst and (h == t.host or t.host.startswith(h.split(".")[0]))), None)
            if p is None:
                continue
            seit = p.get("last_state_change")
            zeit = datetime.fromtimestamp(seit, tz=timezone.utc) if isinstance(seit, (int, float)) else bc
            out.append((t, f"{p['host']}|{p['service']}|{seit}", zeit, p.get("output") or "",
                        {k: v for k, v in p.items() if k != "metadata"} | {"metadata": p.get("metadata")}))
        return out
    return hole


async def graylog_fenster(templates: dict, events: list, kritisch: dict, stumm: set[str],
                          b: datetime) -> None:
    """Aufruf aus der Fehler-Cluster-Erkennung je fertigem Fenster (nur im Echtbetrieb).

    Darf die Erkennung nie stören: jeder Fehler wird protokolliert und geschluckt.
    """
    try:
        verz, d = await _grundlagen()
        treffer = bewerte_graylog(templates, events, kritisch, stumm, verz, d)
        _merke(b, treffer)
        werte = zaehle(treffer, _kontingent, _stunde(b))
        gl = await _graylog_connector()
        if gl is not None:
            werte["historie"] = await _bewahre("graylog", treffer, _graylog_holer(gl, b))
        await _speichere("graylog", b, werte)
    except Exception:
        log.exception("Raster graylog: Fenster %s nicht bewertet", b)


async def _graylog_connector():
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.quellen import lade_quellen
    async with AsyncSessionLocal() as db:
        quellen, _ = await lade_quellen(db)
    return next((q.gl for q in quellen if q.name == "graylog"), None)


async def _wazuh(w, b: datetime) -> list[dict]:
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"range": {"timestamp": {"gte": b.isoformat(), "lt": (b + FENSTER).isoformat()}}},
        "aggs": {"regel": {"terms": {"field": "rule.id", "size": 1000}, "aggs": {
            "agent": {"terms": {"field": "agent.name", "size": 500}, "aggs": {
                "belege": {"top_hits": {"size": KONTINGENT,
                                        "_source": ["agent.labels", "decoder.name", "location"],
                                        "sort": [{"timestamp": "asc"}]}}}},
            "info": {"top_hits": {"size": 1, "_source": ["rule"]}},
        }}},
    }
    async with w._client(timeout=120.0) as client:
        res = await w._query_indexer(client, "wazuh-alerts-*", body)
    return wazuh_zeilen(res.get("aggregations") or {})


async def run_raster() -> None:
    """Wazuh-Fenster und CheckMK-Abgleich bewerten, dann quellenübergreifend zählen."""
    global _checkmk_vorher
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.quellen import lade_quellen

    async with AsyncSessionLocal() as db:
        quellen, fehlend = await lade_quellen(db)
    adapter = {q.name: q for q in quellen}
    verz, d = await _grundlagen()
    jetzt = _jetzt()

    b = _boden(jetzt - WAZUH_VERZUG) - FENSTER
    if "wazuh" in adapter:
        try:
            treffer = bewerte_wazuh(await _wazuh(adapter["wazuh"].w, b), verz, d)
            _merke(b, treffer)
            werte = zaehle(treffer, _kontingent, _stunde(b))
            werte["historie"] = await _bewahre("wazuh", treffer, _wazuh_holer(adapter["wazuh"].w, b))
            await _speichere("wazuh", b, werte)
        except Exception as e:  # noqa: BLE001
            log.warning("Raster wazuh: Fenster %s fehlgeschlagen: %s", b, e)
            await _speichere("wazuh", b, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    else:
        await _speichere("wazuh", b, {"fehler": fehlend.get("wazuh", "nicht geladen")})

    bc = _boden(jetzt)
    if "checkmk" in adapter:
        try:
            if _checkmk_vorher is None:
                _checkmk_vorher = await _letzter_checkmk_zustand(jetzt)
            probleme = await adapter["checkmk"].k.get_problems(include_unknown=True,
                                                               include_handled=True)
            abgleich = bewerte_checkmk(_checkmk_vorher, probleme, verz, d)
            _checkmk_vorher = abgleich.zustand
            _merke(bc, abgleich.treffer)
            werte = zaehle(abgleich.treffer, _kontingent, _stunde(bc))
            werte.update(probleme=len(probleme), behoben=len(abgleich.behoben),
                         erster_abgleich=abgleich.erster,
                         kritikalitaeten=dict(abgleich.kritikalitaeten),
                         zustand=abgleich.zustand)
            werte["historie"] = await _bewahre("checkmk", abgleich.treffer, _checkmk_holer(probleme, bc))
            await _speichere("checkmk", bc, werte)
        except Exception as e:  # noqa: BLE001
            log.warning("Raster checkmk: Abgleich %s fehlgeschlagen: %s", bc, e)
            await _speichere("checkmk", bc, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    else:
        await _speichere("checkmk", bc, {"fehler": fehlend.get("checkmk", "nicht geladen")})

    # Firewall (Graylog, Palo Alto): Bedrohungen einzeln, Verkehr gezählt — gleiches Fenster
    # wie Wazuh (Graylog hat ebenfalls Verzug).
    if "graylog" in adapter:
        from app.services.log_watch import firewall
        try:
            msgs, zaehlung, vollstaendig = await firewall.lies_fenster(adapter["graylog"].gl, b, b + FENSTER)
            treffer = firewall.bewerte_firewall(
                firewall.gruppen(msgs, set(d.p("firewall_externe_zonen"))), verz, d)
            _merke(b, treffer)
            werte = zaehle(treffer, _kontingent, _stunde(b))
            werte.update(bedrohungen=len(msgs), vollstaendig=vollstaendig,
                         verkehr_gesamt=zaehlung.get("gesamt", 0),
                         verkehr_je_aktion=firewall.zusammen(zaehlung.get("aktion", [])),
                         zonen_abgelehnt=zaehlung.get("zonen", [])[:20],
                         zielland_abgelehnt=zaehlung.get("zielland_abgelehnt", [])[:20])
            werte["historie"] = await _bewahre("firewall", treffer, _graylog_holer(adapter["graylog"].gl, b))
            await _speichere("firewall", b, werte)
        except Exception as e:  # noqa: BLE001
            log.warning("Raster firewall: Fenster %s fehlgeschlagen: %s", b, e)
            await _speichere("firewall", b, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})

    # Switches (Graylog nsa/nss/nsc, Level ≤ 4): eigener Musterlerner, gleiches Fenster.
    if "graylog" in adapter:
        from app.services.log_watch import switch
        try:
            treffer, extra = await switch.fenster(adapter["graylog"].gl, b, b + FENSTER, verz, d)
            _merke(b, treffer)
            werte = zaehle(treffer, _kontingent, _stunde(b))
            werte.update(extra)
            werte["historie"] = await _bewahre("switch", treffer, _graylog_holer(adapter["graylog"].gl, b))
            await _speichere("switch", b, werte)
        except Exception as e:  # noqa: BLE001
            log.warning("Raster switch: Fenster %s fehlgeschlagen: %s", b, e)
            await _speichere("switch", b, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})

    # Stufe 0 (Graylog-Verwerfregeln): alle 15 min — je Regel wird jeder Knoten befragt.
    if "graylog" in adapter and bc.minute % 15 == 0:
        from app.services.log_watch import stufe0
        from app.services.log_watch.vokabular import THEMEN
        try:
            regeln, knoten = await stufe0.lies(adapter["graylog"].gl)
            zaehlung = await stufe0.zaehle(adapter["graylog"].gl, regeln, knoten)
            await _speichere("stufe0", bc, {
                "regeln": [{"titel": r["titel"], "orte": r["orte"], "felder": r["felder"],
                            "geaendert": r["geaendert"], **zaehlung.get(r["titel"], {})} for r in regeln],
                "verworfen_pro_stunde": sum(z.get("pro_stunde", 0) for z in zaehlung.values()),
                "widersprueche": stufe0.widersprueche(regeln, THEMEN),
                "themen_ausdruck": stufe0.themen_ausdruck_stand(regeln, THEMEN),
                "vollstaendig": all(z.get("vollstaendig") for z in zaehlung.values()),
            })
        except Exception as e:  # noqa: BLE001
            log.warning("Stufe 0: Lesen fehlgeschlagen: %s", e)
            await _speichere("stufe0", bc, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})

    # Historie: Dokumente, deren Embedding beim Schreiben ausfiel, nachträglich einbetten.
    from app.services.log_watch import historie
    async with AsyncSessionLocal() as db:
        historie_an = await historie.aktiv(db)
    if historie_an:
        try:
            await _speichere("historie", bc, await historie.nachbetten())
        except Exception as e:  # noqa: BLE001
            log.warning("Historie: Nachbetten fehlgeschlagen: %s", e)
            await _speichere("historie", bc, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})

    # Quellenübergreifend: auf Kopien zählen (die Zählung der Fenster bleibt unverändert); bei
    # aktiver Historie bekommen die gespeicherten Nachrichten derselben Hosts R7 nachträglich.
    grenze = jetzt - UEBERGREIFEND - FENSTER
    letzte = [copy.deepcopy(t) for z, t in _juengst if z >= grenze]
    vorher = sum(1 for t in letzte if t.relevanz == 3)
    hosts = quellenuebergreifend(letzte)
    werte = {
        "hosts": sorted(hosts)[:50], "n_hosts": len(hosts),
        "angehoben": sum(1 for t in letzte if t.relevanz == 3) - vorher,
        "zeitraum_min": int(UEBERGREIFEND.total_seconds() // 60),
    }
    if historie_an and hosts:
        je_host = {h: sorted({t.quelle for t in letzte if t.host == h and t.urteil == RELEVANT})
                   for h in hosts}
        try:
            werte["historie"] = await historie.markiere_r7(je_host, grenze)
        except Exception as e:  # noqa: BLE001
            werte["historie"] = {"fehler_text": f"{type(e).__name__}: {str(e)[:300]}"}
    await _speichere("uebergreifend", bc, werte)
    _kontingent.aufraeumen(_stunde(jetzt) - timedelta(hours=1))


def zusammenfassung(zeilen: list[tuple[str, datetime, dict]], stunden: int) -> dict:
    """Trockenlauf-Zeilen → je Quelle Summen und Hochrechnung auf einen Tag.

    Hochgerechnet wird über die tatsächlich abgedeckten Fenster (nicht über `stunden`):
    fehlen Fenster, steht das in `fenster`/`fehler`, statt die Rate zu verfälschen.
    """
    from collections import Counter

    je: dict[str, dict] = {}
    for quelle, _b, w in zeilen:
        z = je.setdefault(quelle, {"fenster": 0, "fehler": 0, "nachrichten": 0,
                                   "gespeichert_waere": 0, "bytes_waere": 0,
                                   "je_relevanz": Counter(), "tags": Counter(),
                                   "je_urteil": Counter(), "je_kriterium": Counter(),
                                   "je_regel": Counter(), "regel_verdraengt": Counter(),
                                   "regel_test": Counter(),
                                   "unbekannte_hosts": set(), "extra": Counter()})
        if "fehler" in w:
            z["fehler"] += 1
            z["letzter_fehler"] = w["fehler"]
            continue
        z["fenster"] += 1
        if quelle == "uebergreifend":
            z["extra"]["hosts_je_lauf"] += w.get("n_hosts", 0)
            z["extra"]["angehoben"] += w.get("angehoben", 0)
            for h in w.get("hosts", []):
                z["tags"][f"host:{h}"] += 1
            continue
        z["nachrichten"] += w.get("nachrichten", 0)
        z["gespeichert_waere"] += w.get("gespeichert_waere", 0)
        z["bytes_waere"] += w.get("bytes_waere", 0)
        z["je_relevanz"].update({k: v for k, v in (w.get("je_relevanz") or {}).items()})
        z["je_urteil"].update(w.get("je_urteil") or {})
        z["je_kriterium"].update(w.get("je_kriterium") or {})
        for k in ("je_regel", "regel_verdraengt", "regel_test"):
            z[k].update(w.get(k) or {})
        z["tags"].update(dict(w.get("top_hashtags") or []))
        z["unbekannte_hosts"] |= set(w.get("unbekannte_hosts") or [])
        for g, n in (w.get("nicht_zugeordnet") or {}).items():
            z["extra"][f"nicht_zugeordnet:{g}"] = max(z["extra"][f"nicht_zugeordnet:{g}"], n)
        for k in ("behoben", "probleme"):
            if k in w:
                z["extra"][k] += w[k]
        for k, v in (w.get("kritikalitaeten") or {}).items():
            z["extra"][f"kritikalitaet:{k}"] = max(z["extra"][f"kritikalitaet:{k}"], v)
    out = {}
    for quelle, z in je.items():
        tage = z["fenster"] * FENSTER.total_seconds() / 86400 or None
        out[quelle] = {
            "fenster": z["fenster"], "fehler": z["fehler"],
            "letzter_fehler": z.get("letzter_fehler"),
            "abgedeckt_h": round(z["fenster"] * FENSTER.total_seconds() / 3600, 2),
            "nachrichten": z["nachrichten"],
            "je_relevanz": {k: z["je_relevanz"].get(k, 0) for k in ("0", "1", "2", "3")},
            "je_urteil": dict(z["je_urteil"]),
            "je_kriterium": dict(sorted(z["je_kriterium"].items())),
            "je_regel": dict(sorted(z["je_regel"].items())),
            "regel_verdraengt": dict(sorted(z["regel_verdraengt"].items())),
            "regel_test": dict(sorted(z["regel_test"].items())),
            "gespeichert_waere": z["gespeichert_waere"],
            "pro_tag": ({"nachrichten": round(z["nachrichten"] / tage),
                         "gespeichert": round(z["gespeichert_waere"] / tage),
                         "mb": round(z["bytes_waere"] / tage / 1e6, 1)} if tage else None),
            "top_hashtags": z["tags"].most_common(25),
            "unbekannte_hosts": sorted(z["unbekannte_hosts"])[:100],
            "n_unbekannte_hosts": len(z["unbekannte_hosts"]),
            "weiteres": dict(z["extra"]),
        }
    return {"stunden": stunden, "quellen": out}
