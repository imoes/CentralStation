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
#: Agentenflotte (Nutzer 06.11.: höchstens 3, nur das eingestellte Modell — strata-27b hat 4 Slots,
#: einer bleibt anderen Nutzern). Ein Agent nimmt Fälle, solange er jünger als AGENT_BUDGET_S ist;
#: LEASE_S löst den Lease eines abgestürzten Agenten (Fall gemessen ≤ 254 s).
AGENTEN = 3
AGENT_BUDGET_S = 480
LEASE_S = 900
#: 22:23-Lauf mit 4 Fällen: 355 s, Warteschlange trotzdem 58 → 91 (R7 hebt seit Teil 13 auch
#: gespeicherte Nachrichten auf 3: 47 von 115 Relevanz-3-Nachrichten/h). strata-27b hat 4 Slots
#: (/slots, gemessen 06.11.) — zwei Fälle zugleich, mit disjunkten Hosts.
HOECHSTENS_JE_LAUF = 8
PARALLEL = 2
#: neue Runden starten nur, solange der Lauf jünger ist — eine Runde dauert gemessen ≤ 180 s
ZEIT_BUDGET_S = 400
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
1. Der Worker hat schon gesucht: du bekommst die Lage des Falls (relevante Nachrichten zum
   Startfilter mit ID und Text, Facetten, frühere Befunde dazu) und je betroffenem Host ein
   Host-Objekt — felder (Stammdaten aus CheckMK), nachbarschaft (Netzplan-Kanten) und gedaechtnis
   (frühere Befunde und Gespräche zu diesem Host). Lies beides zuerst.
   Gibt es im Gedächtnis schon einen Befund zur selben Lage, beziehe dich darauf (Folge-Befund).
   Gespräche mit herkunft "konsole" zeigen, was Menschen in der Konsole zu einem Incident, Alert
   oder Ticket geklärt haben: als Kontext nutzen und nennen, aber nicht als Beleg zitieren.
2. Werkzeuge (lw_suche, lw_facetten, lw_netzplan, lw_nachricht, lw_gespraech) nur zum Vertiefen
   über die Lage hinaus (anderer Filter, anderer Zeitraum, eine Nachricht vollständig):
   den auffälligsten Hashtag als Filter ergänzen, bis ein kleines, zusammenhängendes Ergebnis bleibt.
3. Mit lw_befund abschließen — immer, auch wenn nichts Relevantes bleibt (dann Relevanz 0 oder 1 und
   kurz, warum).

Regeln:
- Belege: nur IDs aus der Lage oder aus lw_suche-Treffern und ein wörtliches Zitat (≥ 8 Zeichen)
  aus deren Text.
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
    ohne Muster (Wazuh, CheckMK) je Host.

    Reihenfolge: dringend zuerst (`signal:dringend`), dann die älteste Nachricht zuerst (wer dem
    Verfall am nächsten ist), dann Größe. Vorher nur nach Größe — gemessen 07.11.: alle 10
    verfallenen Nachrichten waren CRIT auf Prod-Hosts (Einzelfälle, u. a. Domain Controller),
    verdrängt von lauten Fällen mit 26 Nachrichten."""
    je: dict[str, dict] = {}
    for n in offen:
        tags = n.get("hashtags") or []
        muster = sorted(t for t in tags if t.startswith("muster:"))
        start = muster[0] if muster else f"host:{n.get('host', '')}"
        f = je.setdefault(start, {"start": start, "anzahl": 0, "hosts": set(), "dringend": False, "aelteste": ""})
        f["anzahl"] += 1
        f["hosts"].add(n.get("host", ""))
        f["dringend"] = f["dringend"] or "signal:dringend" in tags
        z = n.get("zeit") or ""
        if z and (not f["aelteste"] or z < f["aelteste"]):
            f["aelteste"] = z
    out = [{**f, "hosts": sorted(f["hosts"])} for f in je.values()]
    return sorted(out, key=lambda f: (not f["dringend"], f["aelteste"] or "9", -f["anzahl"], f["start"]))


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


#: Werkzeuge des Trichters. Nicht lw_wissen: frühere Befunde und Gespräche stehen schon im Prompt
#: (Lage des Falls, Gedächtnis je Host) — zwei Wege zum selben Ergebnis nahm die KI beide
#: (gemessen 06.11.: alle 6 Sitzungen nach dem Deploy begannen trotzdem mit lw_wissen).
#: Für die Konsole bleibt lw_wissen über MCP; zum Nachlesen bleibt lw_gespraech.
TRICHTER_WERKZEUGE = ["lw_suche", "lw_facetten", "lw_netzplan", "lw_nachricht", "lw_gespraech",
                      "lw_definition", "lw_befund"]

#: so viele Hosts eines Falls bekommen ein Host-Objekt im Prompt (der Rest wird genannt)
HOSTS_IM_PROMPT = 3


def _hashtags_aus(objekte: list[dict], fall: dict) -> list[str]:
    """Hashtags für die Gesprächszüge: Felder der Hosts (wie an den Nachrichten) und der Fall."""
    tags = {fall["start"]}
    for o in objekte:
        f = o.get("felder") or {}
        for art, feld in (("ort", "ort"), ("kritikalitaet", "kritikalitaet"), ("netz", "netz"),
                          ("netzbereich", "netzbereich")):
            if f.get(feld):
                tags.add(f"{art}:{f[feld]}")
        tags |= {f"hg:{g}" for g in (f.get("hostgruppen") or [])[:5]}
    return sorted(tags)


async def analysiere(fall: dict, llm_config, llm=None, *, bauer=None, schreiber=None, lage_bauer=None) -> dict:
    """Eine Analyse für einen Fall {start, anzahl, hosts}: der Worker baut die Host-Objekte (Suche),
    die KI liest sie als Prompt und vertieft mit Werkzeugen; jeder Zug geht ins Gesprächsprotokoll.
    Ergebnis {start, hosts, sitzung, befund|kein_befund|fehler, schritte}."""
    import json

    from app.services.log_watch import gespraech
    if llm is None:
        from app.services.llm_client import generate_with_tools as llm
    if bauer is None:
        from app.services.log_watch.hostobjekt import baue as bauer
    if lage_bauer is None:
        from app.services.log_watch.hostobjekt import fall_lage as lage_bauer
    schreiber = schreiber or gespraech.schreibe
    sitzung = gespraech.neue_sitzung()
    lage = await lage_bauer(fall["start"], STUNDEN)
    objekte = [await bauer(h, STUNDEN) for h in fall["hosts"][:HOSTS_IM_PROMPT]]
    weitere = fall["hosts"][HOSTS_IM_PROMPT:]
    prompt = (f"Prüfung wahr: {fall['anzahl']} Nachrichten mit Relevanz 3 oder neuem Muster in den letzten "
              f"{STUNDEN} h, noch ohne Analyse. Startfilter: {fall['start']}."
              + (f" Weitere betroffene Hosts ohne Objekt: {', '.join(weitere)}." if weitere else "")
              + "\n\nLage des Falls (Suche des Workers zum Startfilter — rufe lw_suche/lw_facetten/lw_wissen "
              "NICHT erneut mit dem Startfilter auf):\n" + json.dumps(lage, ensure_ascii=False, default=str)
              + "\n\nHost-Objekte (Felder, Nachbarschaft, Gedächtnis):\n"
              + json.dumps(objekte, ensure_ascii=False, default=str))
    verlauf: list[dict] = [{"rolle": "worker", "text": prompt}]
    ergebnis: dict = {}
    try:
        ergebnis = await _schleife(fall, llm_config, llm, prompt, verlauf)
    except Exception as e:  # noqa: BLE001 — das Protokoll wird trotzdem geschrieben
        ergebnis = {"start": fall["start"], "hosts": fall["hosts"], "fehler": f"{type(e).__name__}: {str(e)[:300]}",
                    "schritte": []}
    finally:
        from datetime import datetime as _dt
        try:
            befund_tag = [f"befund:{ergebnis['befund'].split(':', 1)[1]}"] if ergebnis.get("befund") else []
            await schreiber(gespraech.zuege(sitzung, fall, _hashtags_aus(objekte, fall) + befund_tag, verlauf,
                                            _dt.now(timezone.utc)))
        except Exception as e:  # noqa: BLE001 — Protokoll-Ausfall benannt, Analyse bleibt gültig
            ergebnis["protokoll_fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
    return {**ergebnis, "sitzung": sitzung}


async def _schleife(fall: dict, llm_config, llm, prompt: str, verlauf: list[dict]) -> dict:
    nachrichten = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    tools = wz.openai_tools(TRICHTER_WERKZEUGE)
    schritte: list[dict] = []
    erinnert = False
    for _ in range(SCHRITTE):
        t0 = time.monotonic()
        antwort = await llm(llm_config, nachrichten, tools)
        ki_s = round(time.monotonic() - t0, 1)
        nachrichten.append(antwort.nachricht)
        verlauf.append({"rolle": "ki", "ki_s": ki_s, "modell": getattr(llm_config, "model", None),
                        "text": (antwort.text or "") + "".join(
                            f"\n→ {a['name']}({json.dumps(a['argumente'], ensure_ascii=False)[:500]})"
                            for a in antwort.aufrufe)})
        if not antwort.aufrufe:
            if erinnert:
                return {"start": fall["start"], "hosts": fall["hosts"], "kein_befund": "KI hat ohne lw_befund geendet",
                        "text": antwort.text[:1000], "schritte": schritte}
            erinnert = True
            nachrichten.append({"role": "user", "content": "Schließe mit lw_befund ab."})
            verlauf.append({"rolle": "worker", "text": "Schließe mit lw_befund ab."})
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
                verlauf.append({"rolle": "befund", "befund": erg["id"], "werkzeug": a["name"],
                                "text": str(a["argumente"].get("titel", "")) + "\n" + str(a["argumente"].get("befund", ""))})
                return {"start": fall["start"], "hosts": fall["hosts"], "befund": erg["id"], "belege": erg["belege"],
                        "verworfen": erg["verworfen"], "schritte": schritte}
            verlauf.append({"rolle": "werkzeug", "werkzeug": a["name"], "argumente": a["argumente"],
                            "text": _kurz(erg)})
            nachrichten.append({"role": "tool", "tool_call_id": a["id"], "content": _kurz(erg)})
    return {"start": fall["start"], "hosts": fall["hosts"], "kein_befund": f"nach {SCHRITTE} Schritten kein gültiger Befund", "schritte": schritte}


def runden(faelle: list[dict], parallel: int) -> list[list[dict]]:
    """Fälle → Runden zu höchstens `parallel` Fällen mit disjunkten Hosts (Reihenfolge bleibt).
    Zwei Fälle desselben Hosts laufen nie gleichzeitig — sonst entstünden zwei Befunde, die
    voneinander nichts wissen. Rein — testbar."""
    offen = list(faelle)
    out: list[list[dict]] = []
    while offen:
        runde, belegt, rest = [], set(), []
        for f in offen:
            if len(runde) < parallel and not belegt & set(f["hosts"]):
                runde.append(f)
                belegt |= set(f["hosts"])
            else:
                rest.append(f)
        out.append(runde)
        offen = rest
    return out


async def lauf(erzwingen: bool = False, llm=None) -> dict | None:
    """Ein Trichter-Lauf: Prüfung, dann höchstens HOECHSTENS_JE_LAUF Fälle, PARALLEL zugleich
    (disjunkte Hosts), solange das Zeitbudget reicht. Schalter aus und nicht erzwungen → None."""
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_active_llm_config, get_setting
    async with AsyncSessionLocal() as db:
        an = (await get_setting(db, SCHALTER) or "").lower() in ("1", "true", "ja")
        if not (an or erzwingen):
            return None
        llm_config = await get_active_llm_config(db)
    k = await kandidaten()
    ergebnisse: list[dict] = []
    beginn = time.monotonic()

    async def eins(f: dict) -> dict:
        try:
            return await analysiere(f, llm_config, llm)
        except Exception as e:  # noqa: BLE001 — ein Fall scheitert, die anderen laufen
            log.warning("Trichter %s: %s", f["start"], e)
            return {"start": f["start"], "hosts": f["hosts"], "fehler": f"{type(e).__name__}: {str(e)[:300]}"}

    for runde in runden(k["faelle"][:HOECHSTENS_JE_LAUF], PARALLEL):
        if time.monotonic() - beginn > ZEIT_BUDGET_S:
            ergebnisse += [{"start": f["start"], "hosts": f["hosts"],
                            "kein_befund": f"Zeitbudget {ZEIT_BUDGET_S} s des Laufs erschöpft — nächster Lauf"}
                           for f in runde]
            break
        ergebnisse += await asyncio.gather(*(eins(f) for f in runde))
    return {"pruefung": k, "analysen": ergebnisse, "dauer_s": round(time.monotonic() - beginn, 1),
            "parallel": PARALLEL, "befunde": [e["befund"] for e in ergebnisse if "befund" in e]}


# ── Agentenflotte: Kontext steht in der DB, also kann jeder Agent jeden Fall nehmen ────────────

async def belege(fall: dict, agent: str) -> bool:
    """Alle Hosts des Falls für `agent` belegen — atomar alle oder keinen. Abgelaufene Leases
    (abgestürzter Agent) werden vorher gelöst."""
    from datetime import datetime as _dt

    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    jetzt = _dt.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM ki_lease WHERE bis < :j"), {"j": jetzt})
        r = await db.execute(text(
            "INSERT INTO ki_lease (host, agent, fall, seit, bis) "
            "SELECT h, :a, :f, :j, :b FROM unnest(CAST(:hosts AS text[])) AS h "
            "ON CONFLICT (host) DO NOTHING RETURNING host"),
            {"a": agent, "f": fall["start"], "j": jetzt, "b": jetzt + timedelta(seconds=LEASE_S),
             "hosts": list(fall["hosts"])})
        if len(r.fetchall()) != len(set(fall["hosts"])):
            await db.rollback()
            return False
        await db.commit()
        return True


async def gib_frei(agent: str) -> None:
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM ki_lease WHERE agent = :a"), {"a": agent})
        await db.commit()


async def agent(llm=None) -> dict | None:
    """Ein Agent der Flotte: nimmt den nächsten freien Fall (Lease auf seine Hosts), analysiert,
    gibt frei — bis die Warteschlange leer ist oder sein Budget um. Schalter aus → None."""
    import socket

    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_active_llm_config, get_setting
    async with AsyncSessionLocal() as db:
        if (await get_setting(db, SCHALTER) or "").lower() not in ("1", "true", "ja"):
            return None
        llm_config = await get_active_llm_config(db)
    name = f"{socket.gethostname()[:20]}-{uuid_kurz()}"
    beginn = time.monotonic()
    ergebnisse: list[dict] = []
    k: dict = {}
    belegt_von_anderen = 0
    while time.monotonic() - beginn < AGENT_BUDGET_S:
        # Schalter vor JEDEM Fall: aus = Flotte läuft leer (laufende Fälle enden, keine neuen) —
        # so lässt sich ausrollen, ohne eine Analyse abzubrechen
        async with AsyncSessionLocal() as db:
            if (await get_setting(db, SCHALTER) or "").lower() not in ("1", "true", "ja"):
                break
        k = await kandidaten()
        fall = None
        for f in k["faelle"]:
            if await belege(f, name):
                fall = f
                break
            belegt_von_anderen += 1
        if fall is None:
            break
        try:
            ergebnisse.append(await analysiere(fall, llm_config, llm))
        except Exception as e:  # noqa: BLE001
            log.warning("Agent %s, Fall %s: %s", name, fall["start"], e)
            ergebnisse.append({"start": fall["start"], "hosts": fall["hosts"], "fehler": f"{type(e).__name__}: {str(e)[:300]}"})
        finally:
            await gib_frei(name)
    return {"agent": name, "pruefung": k, "analysen": ergebnisse, "dauer_s": round(time.monotonic() - beginn, 1),
            "belegt_von_anderen": belegt_von_anderen, "befunde": [e["befund"] for e in ergebnisse if "befund" in e]}


def uuid_kurz() -> str:
    import uuid
    return uuid.uuid4().hex[:6]
