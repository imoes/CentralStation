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

from app.services.log_watch.raster import (
    Kontingent, Treffer, bewerte_checkmk, bewerte_graylog, bewerte_wazuh,
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


async def _verzeichnis():
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.hosts import verzeichnis

    async with AsyncSessionLocal() as db:
        return await verzeichnis(db)


async def graylog_fenster(templates: dict, events: list, kritisch: dict, stumm: set[str],
                          b: datetime) -> None:
    """Aufruf aus der Fehler-Cluster-Erkennung je fertigem Fenster (nur im Echtbetrieb).

    Darf die Erkennung nie stören: jeder Fehler wird protokolliert und geschluckt.
    """
    try:
        verz = await _verzeichnis()
        treffer = bewerte_graylog(templates, events, kritisch, stumm, verz)
        _merke(b, treffer)
        await _speichere("graylog", b, zaehle(treffer, _kontingent, _stunde(b)))
    except Exception:
        log.exception("Raster graylog: Fenster %s nicht bewertet", b)


async def _wazuh(w, b: datetime) -> list[dict]:
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"range": {"timestamp": {"gte": b.isoformat(), "lt": (b + FENSTER).isoformat()}}},
        "aggs": {"regel": {"terms": {"field": "rule.id", "size": 1000}, "aggs": {
            "agent": {"terms": {"field": "agent.name", "size": 500}},
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
    verz = await _verzeichnis()
    jetzt = _jetzt()

    b = _boden(jetzt - WAZUH_VERZUG) - FENSTER
    if "wazuh" in adapter:
        try:
            treffer = bewerte_wazuh(await _wazuh(adapter["wazuh"].w, b), verz)
            _merke(b, treffer)
            await _speichere("wazuh", b, zaehle(treffer, _kontingent, _stunde(b)))
        except Exception as e:  # noqa: BLE001
            log.warning("Raster wazuh: Fenster %s fehlgeschlagen: %s", b, e)
            await _speichere("wazuh", b, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    else:
        await _speichere("wazuh", b, {"fehler": fehlend.get("wazuh", "nicht geladen")})

    bc = _boden(jetzt)
    if "checkmk" in adapter:
        try:
            probleme = await adapter["checkmk"].k.get_problems(include_unknown=True,
                                                               include_handled=True)
            abgleich = bewerte_checkmk(_checkmk_vorher, probleme, verz)
            _checkmk_vorher = abgleich.zustand
            _merke(bc, abgleich.treffer)
            werte = zaehle(abgleich.treffer, _kontingent, _stunde(bc))
            werte.update(probleme=len(probleme), behoben=len(abgleich.behoben),
                         erster_abgleich=abgleich.erster,
                         kritikalitaeten=dict(abgleich.kritikalitaeten))
            await _speichere("checkmk", bc, werte)
        except Exception as e:  # noqa: BLE001
            log.warning("Raster checkmk: Abgleich %s fehlgeschlagen: %s", bc, e)
            await _speichere("checkmk", bc, {"fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    else:
        await _speichere("checkmk", bc, {"fehler": fehlend.get("checkmk", "nicht geladen")})

    # Quellenübergreifend: auf Kopien zählen — im Trockenlauf wird nichts verändert.
    letzte = [copy.deepcopy(t) for z, t in _juengst if z >= jetzt - UEBERGREIFEND - FENSTER]
    vorher = sum(1 for t in letzte if t.relevanz == 3)
    hosts = quellenuebergreifend(letzte)
    await _speichere("uebergreifend", bc, {
        "hosts": sorted(hosts)[:50], "n_hosts": len(hosts),
        "angehoben": sum(1 for t in letzte if t.relevanz == 3) - vorher,
        "zeitraum_min": int(UEBERGREIFEND.total_seconds() // 60),
    })
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
