"""KI-Trichter: erst wenn die Prüfung wahr ist, arbeitet die KI (Plan „Historie und Analyse als Trichter“).

Prüfung (Skript, ohne KI): in der Historie gibt es Nachrichten mit Relevanz 3 oder `signal:neu`,
die noch keinen Befund haben. Je Host mit solchen Nachrichten läuft eine Werkzeug-Schleife: die KI
fragt zuerst früheres Wissen (lw_wissen), liest Facetten, engt den Filter ein, bis ein kleines
zusammenhängendes Ergebnis bleibt, und legt den Befund mit lw_befund ab. Den Befund prüft und
schreibt der Code (befund.py). Die KI handelt nie an einem System; kein max_tokens.

Jeder Schritt wird festgehalten (Werkzeug, Argumente, Trefferzahl) und steht im Befund unter
`ai_provenance.schritte` — die Begründungskette ist nachlesbar.

Lernen (bekannt.py): nach jedem Befund gilt jeder gesehene Fehler als bekannt. Taucht er wieder auf,
hängt der Agent die Nachricht ohne KI an den Befund — außer sie bringt ein neues Kriterium mit.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from app.services.log_watch import bekannt, rueckfrage
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
   Außerhalb der Historie, wenn es den Befund ändert: get_checkmk_host (besteht das Problem jetzt
   noch?), search_tickets (gibt es ein Ticket? offen = N2 behandelt), search_knowledge_base (Runbook,
   Abhängigkeiten). Deren Ergebnisse sind Kontext — im Befund nennen, aber nie als Beleg zitieren.
   Ist dir die Bedeutung eines Felds oder einer Hashtag-Art unklar: lw_feld. Kennt lw_feld sie nicht,
   lege sie mit lw_feld_anlegen an — nur mit geprüftem Zitat (Nachricht oder gelesene Seite). Weißt du nicht sicher, was eine
   Logzeile bedeutet: web_suche nach Fehlermeldung oder Code (nie Host/IP), dann web_seite — Kontext.
   In Foren steht die Lösung meist im letzten Beitrag.
   Analysiert ein Kollege gerade einen anderen Host, der zu deinem Fall gehört (gleiches Muster, mögliche Ursache
   dort — die Liste „Kollegen“ im Fall nennt, wer woran arbeitet): lw_frage(host, frage, belege) — wie unter Kollegen; seine Antwort ist Kontext, kein Beleg. Eine Rückfrage
   an dich (sie kommt als „Rückfrage … von …“) beantworte sofort mit lw_antwort — nur Belegbares.
3. Mit lw_befund abschließen — immer, auch wenn nichts Relevantes bleibt (dann Relevanz 0 oder 1 und
   kurz, warum).

Regeln:
- Antworte nur mit Fakten, die du belegen kannst — wie im Recherche-Tool mit geprüften Zitaten. Was kein
  Zitat trägt, ist eine Hypothese (mit Status) oder bleibt weg. Das gilt für Befund, Feldbedeutung
  (lw_feld_anlegen) und Wissen aus der Websuche.
- Belege: nur IDs aus der Lage oder aus lw_suche-Treffern und ein wörtliches Zitat (≥ 8 Zeichen)
  aus deren Text.
- kern und fakten behaupten nur, was ein Zitat trägt (Werte, Schwellen, Zeiten). Deutungen darüber
  hinaus („Verzögerung“, „Ausfall droht“) gehören als Hypothese in hypothesen, nicht in den Befund.
- Ursachen sind Hypothesen mit Status offen/gestuetzt/widerlegt. Zeitliche Nähe allein ist kein Beleg
  für eine Ursache; gemeinsames Auftreten heißt so.
- Hashtags: die die Belege tragen. Fehlt eine Art für eine Eigenschaft der Belege, die du belegen kannst
  (z. B. ursache:ansible-rollout, komponente:filebeat), erfinde ein eigenes Tag-Feld: zuerst die Art mit
  lw_feld_anlegen(name=<art>, art="hashtag", bedeutung, belege) registrieren, dann im Befund nennen — es wird an
  die Belege gehängt. Die Arten des Systems (quelle, host, thema, muster …) nur mit Werten der Belege.
- Keine Handlungen an Systemen — du analysierst nur.
- Antworte auf Deutsch, knapp — ein Mensch liest es im Cockpit in Sekunden:
  titel ≤ 80 Zeichen — was und wo; Zahlen und Zeiten nur aus einem Zitat (nicht das Suchfenster).
  kern: EIN Satz ≤ 300 Zeichen — was ist los, wo, seit wann, wie schlimm.
  fakten: 2–6 kurze Punkte, je ein Messwert/Zustand mit Uhrzeit, ohne Wiederholung des kerns.
  folge_von: frühere Befunde (ki:…) zur selben Lage — nie IDs in titel, kern oder fakten.
  hypothesen: je ein Satz."""


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
        "size": PRUEF_MAX, "track_total_hits": True,
        "_source": ["host", "zeit", "hashtags", "schluessel", "kriterien"],
        "query": {"bool": {"filter": [{"range": {"zeit": {"gte": (seit_dt - TAKT).isoformat()}}},
                                      flt.opensearch(AUSLOESER)],
                           "must_not": [{"prefix": {"hashtags": "befund:"}}]}}})
    alle = [{**h["_source"], "_id": h["_id"], "_index": h["_index"]} for h in res["hits"]["hits"]]
    bef = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 500, "_source": ["host", "zeit", "ai_provenance.start"],
        "query": {"bool": {"filter": [{"term": {"quelle": "ki"}}, {"range": {"zeit": {"gte": seit}}}]}}})
    offen_alle = offene(alle, [h["_source"] for h in bef["hits"]["hits"]])
    offen_frist = [n for n in offen_alle if _zeit_iso(n.get("zeit")) >= seit_dt]
    # bekannte Fehler gehen nicht an die KI, sondern an ihren Befund (agent → bekannt.haenge_an)
    offen, anhaengen = bekannt.teile(offen_frist, await bekannt.lade([bekannt.fehler(n) for n in offen_frist]))
    je_host = Counter(n.get("host", "") for n in offen)
    return {"gesamt": len(offen), "gekappt": res["hits"]["total"]["value"] > PRUEF_MAX,
            # im letzten Takt aus dem Prüffenster gefallen, ohne dass eine Analyse sie sah
            "verfallen": len(offen_alle) - len(offen_frist),
            "bekannt": len(anhaengen), "anhaengen": anhaengen,
            # bekannter Fehler mit neuem Kriterium — geht trotzdem an die KI (benannt)
            "neu_seit_befund": sum(1 for n in offen if n.get("neu_seit_befund")),
            "faelle": faelle(offen),
            "hosts": [{"host": h, "anzahl": n} for h, n in je_host.most_common(20)]}


def _zeit_iso(s) -> datetime:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _kurz(name: str, erg: dict) -> str:
    """Werkzeugantwort als Text: erst die Ansicht der Lage (hostobjekt.kuerze_antwort), die
    Zeichengrenze greift nur noch als letzte Sicherung — und benennt, was fehlt."""
    from app.services.log_watch.hostobjekt import kuerze_antwort
    s = json.dumps(kuerze_antwort(name, erg), ensure_ascii=False, default=str)
    return s if len(s) <= ANTWORT_MAX else s[:ANTWORT_MAX] + f' … [abgeschnitten, {len(s)} Zeichen]'


#: Werkzeuge des Trichters. Nicht lw_wissen: frühere Befunde und Gespräche stehen schon im Prompt
#: (Lage des Falls, Gedächtnis je Host) — zwei Wege zum selben Ergebnis nahm die KI beide
#: (gemessen 06.11.: alle 6 Sitzungen nach dem Deploy begannen trotzdem mit lw_wissen).
#: Für die Konsole bleibt lw_wissen über MCP; zum Nachlesen bleibt lw_gespraech.
#: Dazu drei lesende Quellen außerhalb der Historie (Nutzer, 07.11.) — dieselben wie auf dem MCP-Server.
TRICHTER_WERKZEUGE = ["lw_suche", "lw_facetten", "lw_netzplan", "lw_nachricht", "lw_gespraech",
                      "lw_frage", "lw_fragen", "lw_antwort",
                      "lw_definition", "get_checkmk_host", "search_tickets", "search_knowledge_base",
                      "lw_feld", "lw_feld_anlegen", "web_suche", "web_seite", "lw_befund"]

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


async def kollegen(agent: str) -> dict[str, list[str]]:
    """Wer analysiert gerade welche Hosts (ki_lease, ohne den eigenen Agenten und ohne Sonderaufgaben)."""
    from datetime import datetime as _dt

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.log_watch import KiLease
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(KiLease).where(KiLease.bis >= _dt.now(timezone.utc)))).scalars().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        if r.agent != agent and not r.host.startswith("__"):
            out.setdefault(r.agent, []).append(r.host)
    return out


def kollegen_text(k: dict[str, list[str]]) -> str:
    """Zeile für den Fall-Prompt: nur an diese Hosts geht eine Rückfrage an jemanden. Rein — testbar.
    Gemessen 07.11.: 18 Rückfragen an Hosts, die niemand analysierte (14 Agenten, 8 Hosts) — die Agenten
    wussten nicht, wer gerade woran arbeitet."""
    if not k:
        return "Kollegen: gerade analysiert niemand sonst einen Host — lw_frage hätte keinen Empfänger."
    return ("Kollegen (lw_frage erreicht nur diese Hosts): "
            + "; ".join(f"{', '.join(sorted(h)[:6])}" for _, h in sorted(k.items())) + ".")


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
              + json.dumps(objekte, ensure_ascii=False, default=str)
              # nur in der Flotte (fall["_agent"]) — ohne Agent gibt es keine Kollegen
              + ("\n\n" + kollegen_text(await kollegen(fall["_agent"])) if fall.get("_agent") else ""))
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
    if ergebnis.get("befund"):
        # gesehen hat die Analyse die Lage des Falls und, was sie zitiert hat
        try:
            ergebnis["gelernt"] = await bekannt.lerne(
                ergebnis["befund"], ergebnis.get("titel") or "", ergebnis.get("relevanz") or 0,
                ergebnis.get("beleg_ids") or [], [n.get("id") for n in lage.get("nachrichten") or []])
        except Exception as e:  # noqa: BLE001 — Befund bleibt gültig, Lernen benannt ausgefallen
            ergebnis["lern_fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
        try:
            # was die Analyse gesehen hat, hängt am Befund (eine Definition mit offene())
            ergebnis["gesehen"] = await bekannt.markiere_gesehen(
                ergebnis["befund"], ergebnis.get("relevanz"), fall["start"], datetime.now(timezone.utc),
                STUNDEN + TAKT.total_seconds() / 3600)
        except Exception as e:  # noqa: BLE001
            ergebnis["gesehen_fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
    return {**ergebnis, "sitzung": sitzung}


async def _schleife(fall: dict, llm_config, llm, prompt: str, verlauf: list[dict]) -> dict:
    nachrichten = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    fehlt = await wz.nicht_verfuegbar()
    tools = wz.openai_tools([n for n in TRICHTER_WERKZEUGE if n not in fehlt])
    schritte: list[dict] = []
    erinnert = False
    agent_name = fall.get("_agent") or ""
    for _ in range(SCHRITTE):
        # Rückfragen anderer Instanzen (rueckfrage.py) vor dem nächsten Modellaufruf zustellen — mit Kontext
        if agent_name:
            for f in await rueckfrage.posteingang(agent_name):
                zug = rueckfrage.als_zug(f)
                nachrichten.append({"role": "user", "content": zug})
                verlauf.append({"rolle": "worker", "text": zug})
            # … und Antworten auf eigene Rückfragen, die nach dem Warten eintrafen
            for f in await rueckfrage.antworten_eingang(agent_name):
                zug = rueckfrage.antwort_als_zug(f)
                nachrichten.append({"role": "user", "content": zug})
                verlauf.append({"rolle": "worker", "text": zug})
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
            elif a["name"] in ("lw_frage", "lw_fragen", "lw_antwort"):
                extra = {"_agent": agent_name} | ({"_fall": fall["start"]} if a["name"] == "lw_frage" else {})
                erg = await wz.rufe(a["name"], a["argumente"], **extra)
            else:
                erg = await wz.rufe(a["name"], a["argumente"])
            schritte.append({"werkzeug": a["name"], "argumente": a["argumente"], "ki_s": ki_s,
                             "gesamt": erg.get("gesamt"), "fehler": erg.get("fehler") or erg.get("abgelehnt")})
            if a["name"] == "lw_befund" and "id" in erg:
                verlauf.append({"rolle": "befund", "befund": erg["id"], "werkzeug": a["name"],
                                "text": str(a["argumente"].get("titel", "")) + "\n" + str(a["argumente"].get("kern", ""))
                                + "".join(f"\n- {f}" for f in a["argumente"].get("fakten") or [])})
                arg = a["argumente"]
                return {"start": fall["start"], "hosts": fall["hosts"], "befund": erg["id"], "belege": erg["belege"],
                        "verworfen": erg["verworfen"], "schritte": schritte,
                        "titel": str(arg.get("titel") or ""), "relevanz": arg.get("relevanz"),
                        "beleg_ids": [str(b.get("id")) for b in arg.get("belege") or [] if isinstance(b, dict)]}
            verlauf.append({"rolle": "werkzeug", "werkzeug": a["name"], "argumente": a["argumente"],
                            "text": _kurz(a["name"], erg)})
            nachrichten.append({"role": "tool", "tool_call_id": a["id"], "content": _kurz(a["name"], erg)})
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


#: Feld-Doku als Aufgabe der Flotte: ein Agent übernimmt sie zwischen zwei Fällen. Gemessen 07.11.: als eigener
#: Job (nur bei freiem Platz) setzte sie 14:47, 15:17, 15:47, 16:47 und 17:17 aus — die Flotte ist fast immer voll
FELD_DOKU_LEASE = "__feld-doku__"
FELD_DOKU_ABSTAND_MIN = 30


async def feld_doku_faellig() -> bool:
    """Letzter ERFOLGREICHER Lauf (nicht ausgesetzt, ohne Fehler) mindestens FELD_DOKU_ABSTAND_MIN alt."""
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.lauf import faellig
    async with AsyncSessionLocal() as db:
        # ein erfolgreicher Lauf trägt "fehler": [] (Liste, ggf. leer); ein gescheiterter "fehler": "<Text>".
        # Gemessen 07.11.: mit NOT (werte ? 'fehler') galt kein Lauf als erfolgreich → lief bei jedem Fallwechsel
        zuletzt = (await db.execute(text(
            "SELECT max(created_at) FROM raster_lauf WHERE quelle = 'feld-doku' "
            "AND NOT (werte ? 'ausgesetzt') AND coalesce(jsonb_typeof(werte->'fehler'), '') <> 'string'"))).scalar()
    return faellig(zuletzt, datetime.now(timezone.utc), FELD_DOKU_ABSTAND_MIN)


async def feld_doku_als_aufgabe(agent_name: str) -> dict | None:
    """Ist die Feld-Doku fällig und frei, erledigt dieser Agent sie (Sperre FELD_DOKU_LEASE: nur einer)."""
    if not await feld_doku_faellig():
        return None
    if not await belege({"start": "feld-doku", "hosts": [FELD_DOKU_LEASE]}, agent_name):
        return None
    from app.services.log_watch import felder
    from app.services.log_watch.lauf import _speichere
    b = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    try:
        erg = await felder.beschreibe_quellfelder()
        await _speichere("feld-doku", b, {**erg, "agent": agent_name})
        return erg
    except Exception as e:  # noqa: BLE001
        await _speichere("feld-doku", b, {"fehler": f"{type(e).__name__}: {str(e)[:300]}", "agent": agent_name})
        return None
    finally:
        await gib_frei(agent_name)


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
    angehaengt = 0
    while time.monotonic() - beginn < AGENT_BUDGET_S:
        # Schalter vor JEDEM Fall: aus = Flotte läuft leer (laufende Fälle enden, keine neuen) —
        # so lässt sich ausrollen, ohne eine Analyse abzubrechen
        async with AsyncSessionLocal() as db:
            if (await get_setting(db, SCHALTER) or "").lower() not in ("1", "true", "ja"):
                break
        await feld_doku_als_aufgabe(name)
        k = await kandidaten()
        if k["anhaengen"]:
            angehaengt += (await bekannt.haenge_an(k.pop("anhaengen")))["angehaengt"]
        k.pop("anhaengen", None)
        fall = None
        for f in k["faelle"]:
            if await belege(f, name):
                fall = f
                break
            belegt_von_anderen += 1
        if fall is None:
            break
        try:
            fall["_agent"] = name
            ergebnisse.append(await analysiere(fall, llm_config, llm))
        except Exception as e:  # noqa: BLE001
            log.warning("Agent %s, Fall %s: %s", name, fall["start"], e)
            ergebnisse.append({"start": fall["start"], "hosts": fall["hosts"], "fehler": f"{type(e).__name__}: {str(e)[:300]}"})
        finally:
            await rueckfrage.schliesse_offene(name)
            await gib_frei(name)
    return {"agent": name, "pruefung": k, "analysen": ergebnisse, "dauer_s": round(time.monotonic() - beginn, 1),
            "belegt_von_anderen": belegt_von_anderen, "befunde": [e["befund"] for e in ergebnisse if "befund" in e],
            # Wiederholungen bekannter Fehler, ohne KI an ihren Befund gehängt
            "bekannt_angehaengt": angehaengt}


def uuid_kurz() -> str:
    import uuid
    return uuid.uuid4().hex[:6]
