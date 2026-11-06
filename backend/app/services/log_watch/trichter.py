"""KI-Trichter: erst wenn die Prüfung wahr ist, arbeitet die KI (Plan „Historie und Analyse als Trichter“).

Prüfung (Skript, ohne KI): in der Historie gibt es Nachrichten mit Relevanz 3 oder `signal:neu`,
die noch keinen Befund haben. Je Host mit solchen Nachrichten läuft eine Werkzeug-Schleife: die KI
fragt zuerst früheres Wissen (lw_wissen), liest Facetten, engt den Filter ein, bis ein kleines
zusammenhängendes Ergebnis bleibt, und legt den Befund mit lw_befund ab. Den Befund prüft und
schreibt der Code (befund.py). Die KI handelt nie an einem System; kein max_tokens.

Jeder Schritt wird festgehalten (Werkzeug, Argumente, Trefferzahl) und steht im Befund unter
`ai_provenance.schritte` — die Begründungskette ist nachlesbar.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from app.services.log_watch import werkzeuge as wz

log = logging.getLogger(__name__)

SCHALTER = "log_watch.ki_aktiv"
#: Nachrichten dieser Prüfung lösen eine Analyse aus (Filtersprache)
AUSLOESER = "(relevanz>=3 OR signal:neu) !quelle:(ki OR konsole)"
STUNDEN = 2
HOECHSTENS_JE_LAUF = 2
SCHRITTE = 12
#: so viel einer Werkzeugantwort geht an die KI (Rest benannt abgeschnitten)
ANTWORT_MAX = 6000

#: statisch — keine Zeit, keine IDs (Prompt-Cache, globale Regel)
SYSTEM = """Du bist die Analyse-KI der Log-Überwachung. Du untersuchst relevante Lognachrichten aus
Graylog, Wazuh, CheckMK, Firewall und Switches. Die Nachrichten sind vom Raster vorklassifiziert:
Relevanz 0–3, Hashtags art:wert (host:, thema:, signal:, quelle:, kritikalitaet:, muster:, regel: …),
Kriterien R1–R8 (relevant) / N0–N6 (nicht relevant) mit Gründen.

Vorgehen als Trichter:
1. Zuerst lw_wissen mit dem Host-Filter: gibt es schon einen Befund oder gelöstes Konsolen-Wissen?
   Wenn ja und die Lage ist unverändert, beziehe dich darauf.
2. lw_facetten bzw. lw_suche mit breitem Filter, Facetten lesen.
3. Den auffälligsten Hashtag als Filter ergänzen, erneut suchen — bis ein kleines, zusammenhängendes
   Ergebnis übrig bleibt (oder nichts Relevantes).
4. Mit lw_befund abschließen — immer, auch wenn nichts Relevantes bleibt (dann Relevanz 0 oder 1 und
   kurz, warum).

Regeln:
- Belege: nur IDs aus lw_suche-Treffern und ein wörtliches Zitat (≥ 8 Zeichen) aus deren Text.
- Der Befund-Text behauptet nur, was ein Zitat trägt (Werte, Schwellen, Zeiten). Deutungen darüber
  hinaus („Verzögerung“, „Ausfall droht“) gehören als Hypothese in hypothesen, nicht in den Befund.
- Ursachen sind Hypothesen mit Status offen/gestuetzt/widerlegt. Zeitliche Nähe allein ist kein Beleg
  für eine Ursache; gemeinsames Auftreten heißt so.
- Hashtags nur, die die Belege tragen. Keine Handlungen an Systemen — du analysierst nur.
- Antworte auf Deutsch, knapp: Titel ≤ 80 Zeichen — was und wo, Zahlen und Zeiten nur aus einem
  Zitat (nicht das Suchfenster); Befund ≤ 8 Sätze mit Zahlen und Zeitraum."""


async def kandidaten(stunden: float = STUNDEN) -> dict:
    """Prüfung: Hosts mit auslösenden Nachrichten ohne Befund. {gesamt, hosts:[{host, anzahl}]}."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    from app.services.log_watch.historie import PRAEFIX
    bis = datetime.now(timezone.utc)
    body = {"size": 0, "track_total_hits": True, "query": {"bool": {
        "filter": [{"range": {"zeit": {"gte": (bis - timedelta(hours=stunden)).isoformat()}}},
                   flt.opensearch(AUSLOESER)],
        "must_not": [{"prefix": {"hashtags": "befund:"}}]}},
        "aggs": {"hosts": {"terms": {"field": "host", "size": 20}}}}
    res = await get_opensearch().search(index=f"{PRAEFIX}*", body=body, ignore_unavailable=True)
    return {"gesamt": res["hits"]["total"]["value"],
            "hosts": [{"host": b["key"], "anzahl": b["doc_count"]}
                      for b in res["aggregations"]["hosts"]["buckets"]]}


def _kurz(erg: dict) -> str:
    s = json.dumps(erg, ensure_ascii=False, default=str)
    return s if len(s) <= ANTWORT_MAX else s[:ANTWORT_MAX] + f' … [abgeschnitten, {len(s)} Zeichen]'


async def analysiere(host: str, anzahl: int, llm_config, llm=None) -> dict:
    """Eine Werkzeug-Schleife für einen Host. Ergebnis {host, befund|kein_befund|fehler, schritte}."""
    if llm is None:
        from app.services.llm_client import generate_with_tools as llm
    nachrichten = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"Prüfung wahr: {anzahl} Nachrichten mit Relevanz 3 oder neuem Muster auf Host {host} in den "
            f"letzten {STUNDEN} h, noch ohne Befund. Startfilter: host:{host}. Untersuche und lege den "
            f"Befund ab.")},
    ]
    tools = wz.openai_tools()
    schritte: list[dict] = []
    erinnert = False
    for _ in range(SCHRITTE):
        t0 = time.monotonic()
        antwort = await llm(llm_config, nachrichten, tools)
        ki_s = round(time.monotonic() - t0, 1)
        nachrichten.append(antwort.nachricht)
        if not antwort.aufrufe:
            if erinnert:
                return {"host": host, "kein_befund": "KI hat ohne lw_befund geendet",
                        "text": antwort.text[:1000], "schritte": schritte}
            erinnert = True
            nachrichten.append({"role": "user", "content": "Schließe mit lw_befund ab."})
            continue
        for a in antwort.aufrufe:
            if a["fehler"]:
                erg = {"fehler": a["fehler"]}
            elif a["name"] == "lw_befund":
                erg = await wz.rufe(a["name"], a["argumente"], _modell=getattr(llm_config, "model", ""),
                                    _schritte=list(schritte))
            else:
                erg = await wz.rufe(a["name"], a["argumente"])
            schritte.append({"werkzeug": a["name"], "argumente": a["argumente"], "ki_s": ki_s,
                             "gesamt": erg.get("gesamt"), "fehler": erg.get("fehler") or erg.get("abgelehnt")})
            if a["name"] == "lw_befund" and "id" in erg:
                return {"host": host, "befund": erg["id"], "belege": erg["belege"],
                        "verworfen": erg["verworfen"], "schritte": schritte}
            nachrichten.append({"role": "tool", "tool_call_id": a["id"], "content": _kurz(erg)})
    return {"host": host, "kein_befund": f"nach {SCHRITTE} Schritten kein gültiger Befund", "schritte": schritte}


async def lauf(erzwingen: bool = False, llm=None) -> dict | None:
    """Ein Trichter-Lauf: Prüfung, dann je Host (höchstens HOECHSTENS_JE_LAUF) eine Analyse.
    Schalter aus und nicht erzwungen → None."""
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_active_llm_config, get_setting
    async with AsyncSessionLocal() as db:
        an = (await get_setting(db, SCHALTER) or "").lower() in ("1", "true", "ja")
        if not (an or erzwingen):
            return None
        llm_config = await get_active_llm_config(db)
    k = await kandidaten()
    ergebnisse = []
    for h in k["hosts"][:HOECHSTENS_JE_LAUF]:
        try:
            ergebnisse.append(await analysiere(h["host"], h["anzahl"], llm_config, llm))
        except Exception as e:  # noqa: BLE001 — ein Host scheitert, der nächste läuft
            log.warning("Trichter %s: %s", h["host"], e)
            ergebnisse.append({"host": h["host"], "fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    return {"pruefung": k, "analysen": ergebnisse,
            "befunde": [e["befund"] for e in ergebnisse if "befund" in e]}
