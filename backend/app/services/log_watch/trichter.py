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
#: Fälle je Lauf — gemessen 06.11.: 56–101 s Modellzeit je Fall; mit 2 Fällen je 10 min wuchs die
#: Warteschlange (20 → 58 Nachrichten in 40 min). ZEIT_BUDGET_S hält einen Lauf unter dem Takt.
HOECHSTENS_JE_LAUF = 4
ZEIT_BUDGET_S = 480
#: Takt des Jobs (scheduler: minute 3-59/10) — Nachrichten, die in diesem Abstand aus dem
#: Prüffenster fallen, ohne gesehen zu sein, heißen „verfallen“ (benannt, nicht still)
TAKT = timedelta(minutes=10)
SCHRITTE = 12
#: so viel einer Werkzeugantwort geht an die KI (Rest benannt abgeschnitten)
ANTWORT_MAX = 6000

#: statisch — keine Zeit, keine IDs (Prompt-Cache, globale Regel)
SYSTEM = """Du bist die Analyse-KI der Log-Überwachung. Du untersuchst relevante Lognachrichten aus
Graylog, Wazuh, CheckMK, Firewall und Switches. Die Nachrichten sind vom Raster vorklassifiziert:
Relevanz 0–3, Hashtags art:wert (host:, thema:, signal:, quelle:, kritikalitaet:, muster:, regel: …),
Kriterien R1–R8 (relevant) / N0–N6 (nicht relevant) mit Gründen.

Vorgehen als Trichter:
1. Zuerst lw_wissen mit dem Startfilter und je betroffenem Host (host:…): gibt es schon einen Befund
   oder gelöstes Konsolen-Wissen?
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


#: so viele auslösende Nachrichten liest die Prüfung je Lauf (mehr wären ein Rückstau, benannt)
PRUEF_MAX = 2000


def _start_von(befund: dict) -> str:
    """Startfilter einer Analyse; ältere Befunde ohne gespeicherten Start: der Host."""
    return (befund.get("ai_provenance") or {}).get("start") or f"host:{befund.get('host', '')}"


def offene(nachrichten: list[dict], befunde: list[dict]) -> list[dict]:
    """Auslösende Nachrichten, die noch keine Analyse gesehen hat. Rein — testbar.

    Eine Analyse hat alles gesehen, was ihr Startfilter (ein Hashtag) bis zu ihrem Zeitpunkt
    traf: eine Nachricht ist erledigt, wenn ein späterer Befund mit einem Start existiert, den
    sie trägt. Ohne diese Regel stand jede nicht zitierte Nachricht im nächsten Lauf wieder an
    (gemessen 06.11.: cue0149, cue0174, cue222 erneut wartend, alle älter als ihr Befund)."""
    starts = [(_start_von(b), b.get("zeit") or "") for b in befunde]
    return [n for n in nachrichten
            if not any(st in (n.get("hashtags") or []) and z >= (n.get("zeit") or "") for st, z in starts)]


def faelle(offen: list[dict]) -> list[dict]:
    """Offene Nachrichten → Fälle. Gleiches Muster = ein Fall über alle Hosts (ein Problem, ein
    Befund — gemessen 06.11.: derselbe ExifTool-Fehler auf cue0174 und cue222 gab zwei Befunde);
    ohne Muster (Wazuh, CheckMK) je Host. Größter Fall zuerst."""
    je: dict[str, dict] = {}
    for n in offen:
        tags = n.get("hashtags") or []
        muster = sorted(t for t in tags if t.startswith("muster:"))
        start = muster[0] if muster else f"host:{n.get('host', '')}"
        f = je.setdefault(start, {"start": start, "anzahl": 0, "hosts": set()})
        f["anzahl"] += 1
        f["hosts"].add(n.get("host", ""))
    out = [{**f, "hosts": sorted(f["hosts"])} for f in je.values()]
    return sorted(out, key=lambda f: (-f["anzahl"], f["start"]))


async def kandidaten(stunden: float = STUNDEN) -> dict:
    """Prüfung: offene auslösende Nachrichten, als Fälle. {gesamt, faelle, hosts, gekappt}."""
    from collections import Counter

    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    from app.services.log_watch.historie import PRAEFIX
    os_client = get_opensearch()
    seit_dt = datetime.now(timezone.utc) - timedelta(hours=stunden)
    seit = seit_dt.isoformat()
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": PRUEF_MAX, "track_total_hits": True, "_source": ["host", "zeit", "hashtags"],
        "query": {"bool": {"filter": [{"range": {"zeit": {"gte": (seit_dt - TAKT).isoformat()}}},
                                      flt.opensearch(AUSLOESER)],
                           "must_not": [{"prefix": {"hashtags": "befund:"}}]}}})
    alle = [h["_source"] for h in res["hits"]["hits"]]
    bef = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 500, "_source": ["host", "zeit", "ai_provenance.start"],
        "query": {"bool": {"filter": [{"term": {"quelle": "ki"}}, {"range": {"zeit": {"gte": seit}}}]}}})
    offen_alle = offene(alle, [h["_source"] for h in bef["hits"]["hits"]])
    offen = [n for n in offen_alle if _zeit_iso(n.get("zeit")) >= seit_dt]
    je_host = Counter(n.get("host", "") for n in offen)
    return {"gesamt": len(offen), "gekappt": res["hits"]["total"]["value"] > PRUEF_MAX,
            # im letzten Takt aus dem Prüffenster gefallen, ohne dass eine Analyse sie sah
            "verfallen": len(offen_alle) - len(offen),
            "faelle": faelle(offen),
            "hosts": [{"host": h, "anzahl": n} for h, n in je_host.most_common(20)]}


def _zeit_iso(s) -> datetime:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _kurz(erg: dict) -> str:
    s = json.dumps(erg, ensure_ascii=False, default=str)
    return s if len(s) <= ANTWORT_MAX else s[:ANTWORT_MAX] + f' … [abgeschnitten, {len(s)} Zeichen]'


async def analysiere(fall: dict, llm_config, llm=None) -> dict:
    """Eine Werkzeug-Schleife für einen Fall {start, anzahl, hosts}.
    Ergebnis {start, hosts, befund|kein_befund|fehler, schritte}."""
    if llm is None:
        from app.services.llm_client import generate_with_tools as llm
    nachrichten = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"Prüfung wahr: {fall['anzahl']} Nachrichten mit Relevanz 3 oder neuem Muster in den letzten "
            f"{STUNDEN} h, noch ohne Analyse. Startfilter: {fall['start']} — betroffene Hosts: "
            f"{', '.join(fall['hosts'])}. Untersuche und lege den Befund ab.")},
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
                return {"start": fall["start"], "hosts": fall["hosts"], "kein_befund": "KI hat ohne lw_befund geendet",
                        "text": antwort.text[:1000], "schritte": schritte}
            erinnert = True
            nachrichten.append({"role": "user", "content": "Schließe mit lw_befund ab."})
            continue
        for a in antwort.aufrufe:
            if a["fehler"]:
                erg = {"fehler": a["fehler"]}
            elif a["name"] == "lw_befund":
                erg = await wz.rufe(a["name"], a["argumente"], _modell=getattr(llm_config, "model", ""),
                                    _schritte=list(schritte), _start=fall["start"])
            else:
                erg = await wz.rufe(a["name"], a["argumente"])
            schritte.append({"werkzeug": a["name"], "argumente": a["argumente"], "ki_s": ki_s,
                             "gesamt": erg.get("gesamt"), "fehler": erg.get("fehler") or erg.get("abgelehnt")})
            if a["name"] == "lw_befund" and "id" in erg:
                return {"start": fall["start"], "hosts": fall["hosts"], "befund": erg["id"], "belege": erg["belege"],
                        "verworfen": erg["verworfen"], "schritte": schritte}
            nachrichten.append({"role": "tool", "tool_call_id": a["id"], "content": _kurz(erg)})
    return {"start": fall["start"], "hosts": fall["hosts"], "kein_befund": f"nach {SCHRITTE} Schritten kein gültiger Befund", "schritte": schritte}


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
    beginn = time.monotonic()
    for f in k["faelle"][:HOECHSTENS_JE_LAUF]:
        if time.monotonic() - beginn > ZEIT_BUDGET_S:
            ergebnisse.append({"start": f["start"], "hosts": f["hosts"],
                               "kein_befund": f"Zeitbudget {ZEIT_BUDGET_S} s des Laufs erschöpft — nächster Lauf"})
            break
        try:
            ergebnisse.append(await analysiere(f, llm_config, llm))
        except Exception as e:  # noqa: BLE001 — ein Fall scheitert, der nächste läuft
            log.warning("Trichter %s: %s", f["start"], e)
            ergebnisse.append({"start": f["start"], "hosts": f["hosts"], "fehler": f"{type(e).__name__}: {str(e)[:300]}"})
    return {"pruefung": k, "analysen": ergebnisse, "dauer_s": round(time.monotonic() - beginn, 1),
            "befunde": [e["befund"] for e in ergebnisse if "befund" in e]}
