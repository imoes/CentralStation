"""Werkzeuge der Log-Überwachung — eine Registry für KI-Trichter und MCP-Server.

Der Agent sieht dasselbe wie der Mensch im Cockpit: dieselbe Suche, dieselben Facetten, dieselbe
Definition. Alle Werkzeuge lesen; einzig `lw_befund` schreibt — die Analyse der KI als Dokument
`quelle:ki`, keine Aktion an einem System (Belege prüft der Code, befund.py).

Drei Werkzeuge reichen über die Historie hinaus (Nutzer, 07.11.): `get_checkmk_host`,
`search_tickets`, `search_knowledge_base`. Sie heißen wie auf dem MCP-Server und rufen dieselbe
Umsetzung (checkmk_metrics.host_status, ticketing.search_tickets, aikb.search_kb) — nur lesend,
ohne Tiefensuche, mit Frist EXTERN_S. Belege eines Befunds bleiben Nachrichten der Historie.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from app.services.log_watch import suche as s

#: Filtersprache in einem Satz — steht in jeder Werkzeugbeschreibung, die einen Filter nimmt
FILTER_HILFE = ("Filtersprache: art:wert (Hashtag, z. B. host:docker0218, thema:oom, quelle:wazuh, "
                "signal:neu), art:(a OR b), art:wert*, !art:wert, OR, Klammern, level>=7, relevanz>=3, "
                "@Objekt, \"freier Text\". Leer = alles.")


async def _objekte() -> dict[str, str]:
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.definition import aktuelle
    async with AsyncSessionLocal() as db:
        return dict((await aktuelle(db)).objekte or {})


async def lw_suche(filter: str = "", stunden: float = 24, limit: int = 20, bedeutung: str = "") -> dict:
    return await s.suche(filter, stunden=stunden, limit=limit, bedeutung=bedeutung,
                         objekte=await _objekte())


async def lw_facetten(filter: str = "", stunden: float = 24) -> dict:
    r = await s.suche(filter, stunden=stunden, limit=0, objekte=await _objekte())
    return {k: r[k] for k in ("filter", "zeitraum", "gesamt", "facetten", "fehler") if k in r}


async def lw_netzplan(filter: str = "", stunden: float = 24, knoten: int = 15) -> dict:
    return await s.netzplan(filter, stunden=stunden, knoten=knoten, objekte=await _objekte())


async def lw_gespraech(filter: str, tage: float = 7, sitzungen: int = 3) -> dict:
    """Gesprächsprotokoll(e) früherer Analysen zum Filter (z. B. host:x) — Laden ist eine Suche."""
    from app.services.log_watch import filter as flt
    from app.services.log_watch import gespraech
    try:
        return {"filter": filter, "sitzungen": await gespraech.lade(filter, tage=tage, sitzungen=max(1, min(sitzungen, 10)))}
    except flt.FilterFehler as e:
        return {"filter": filter, "fehler": str(e)}


async def lw_hostverzeichnis(name: str = "") -> dict:
    """Das Hostverzeichnis (CheckMK, 1 h zwischengespeichert) — dieselbe Zuordnung, aus der Raster, Firewall und
    Switches ihre Hashtags host:/ort:/kritikalitaet:/hg:/netz:/netzbereich: bilden. Mit `name`: nur die Zuordnung
    dieses Namens (kanonischer Name oder benannter Grund unbekannt | mehrdeutig | container-id)."""
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch import hosts as h
    async with AsyncSessionLocal() as db:
        v = await h.verzeichnis(db)
    kopf = {"quelle": "CheckMK-Hostliste (Konfiguration + Livestatus-Adresse), services/log_watch/hosts.py",
            "alter_s": h.alter_s(), "cache_s": h.CACHE_S}
    if name.strip():
        e, k, grund = v.zuordnen(name)
        if e is None:
            return {**kopf, "name": name, "schluessel": k, "zugeordnet": False, "grund": grund,
                    "hashtags": [f"host:{k}", f"hostliste:{grund}"] if k else []}
        return {**kopf, "name": name, "schluessel": k, "zugeordnet": True,
                "host": next(x for x in v.als_liste() if x["name"] == e.name)}
    liste = v.als_liste()
    if not liste:
        return {**kopf, "anzahl": 0, "hosts": [], "zustand": "leer: kein CheckMK-Connector aktiv oder CheckMK nicht "
                "erreichbar — alle Hosts gelten dann als hostliste:unbekannt"}
    return {**kopf, "anzahl": len(liste), "mehrdeutige_kurznamen": sorted(v.mehrdeutig),
            "gruende": [h.UNBEKANNT, h.MEHRDEUTIG, h.CONTAINER_ID], "hosts": liste}


async def lw_nachricht(id: str) -> dict:
    """Eine Nachricht der Historie vollständig (mit Originalfeldern `roh`)."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    res = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 1, "query": {"ids": {"values": [id]}}, "_source": {"excludes": ["vektor"]}})
    hits = res["hits"]["hits"]
    return {"id": id, **hits[0]["_source"]} if hits else {"id": id, "fehler": "keine Nachricht mit dieser ID"}


async def lw_wissen(filter: str = "", bedeutung: str = "", stunden: float = 24 * 30, limit: int = 10) -> dict:
    """Frühere Befunde der KI und Konsolen-Wissen zu einem Filter (oder nach Bedeutung)."""
    f = "quelle:(ki OR konsole)" + (f" ({filter})" if filter.strip() else "")
    return await s.suche(f, stunden=stunden, limit=limit, bedeutung=bedeutung, objekte=await _objekte(),
                         sortierung="zeit")


async def lw_feld(name: str = "") -> dict:
    """Bedeutung eines Felds, einer Hashtag-Art oder eines Signals nachschlagen (Tabelle feld_bedeutung)."""
    from app.services.log_watch import felder
    return await felder.nachschlagen(name)


async def lw_feld_anlegen(name: str, art: str, bedeutung: str, belege: list, beispiel: str = "") -> dict:
    """Neue Bedeutung anlegen — nur mit geprüften Zitaten (nur NEUE Namen; Vorhandenes ändert nur ein Mensch)."""
    from app.services.log_watch import felder
    ok, gruende = await felder.pruefe_belege_feld(belege)
    if not ok:
        return {"abgelehnt": "kein geprüftes Zitat — " + "; ".join(gruende[:4])}
    quelle = "geprüfte Zitate: " + "; ".join(f"„{b['zitat']}“ ({b.get('id') or b.get('url')})" for b in ok)
    return {**await felder.anlegen(name, art, bedeutung, beispiel, quelle, "ki"),
            **({"verworfen": gruende} if gruende else {})}


async def web_suche(query: str, warum: str) -> dict:
    from app.services.log_watch import websuche
    return await websuche.suche(query, warum)


async def web_seite(url: str) -> dict:
    from app.services.log_watch import websuche
    return await websuche.seite(url)


async def lw_frage(host: str, frage: str, belege: list | None = None, _agent: str = "", _fall: str = "") -> dict:
    """Rückfrage an den Agenten, der `host` gerade analysiert (wie Peer-Sessions) — rueckfrage.py."""
    from app.services.log_watch import rueckfrage
    return await rueckfrage.stelle(_agent or "unbekannt", host, frage,
                                   {"fall": _fall, "belege": [str(b) for b in belege or []][:10]})


async def lw_fragen(id: str = "", host: str = "", _agent: str = "") -> dict:
    """Eine Rückfrage (mit Antwort) oder die eigenen der letzten 24 h."""
    from app.services.log_watch import rueckfrage
    if id:
        return await rueckfrage.lies(id) or {"fehler": f"Frage {id} unbekannt"}
    return {"fragen": await rueckfrage.liste(von=_agent or None, host=host or None)}


async def lw_antwort(id: str, antwort: str, belege: list | None = None, _agent: str = "") -> dict:
    from app.services.log_watch import rueckfrage
    return await rueckfrage.antworte(_agent, id, antwort, belege)


async def lw_definition() -> dict:
    from app.core.database import AsyncSessionLocal
    from app.services.log_watch.definition import KRITERIEN, aktuelle
    async with AsyncSessionLocal() as db:
        d = await aktuelle(db)
    return {"version": d.version,
            "kriterien": [{"id": k.id, "art": k.art, "titel": k.titel, "beschreibung": k.beschreibung}
                          for k in KRITERIEN],
            "objekte": d.objekte,
            "regeln": [{"id": r.id, "name": r.name, "filter": r.filter, "aktion": r.aktion,
                        "kriterium": r.kriterium, "modus": r.modus} for r in (d.regeln or [])]}


async def lw_befund(titel: str, kern: str, relevanz: int, belege: list, fakten: list | None = None,
                    folge_von: list | None = None, hypothesen: list | None = None,
                    hashtags: list | None = None, vi_half: list | None = None, vi_falsch: list | None = None,
                    _modell: str = "", _schritte: list | None = None, _start: str = "") -> dict:
    # vi_half/vi_falsch: Rückmeldung zu den Kollektiv-Hinweisen — liest der Trichter aus den Argumenten und schickt sie
    # nach vi_nachher als vi_feedback (vi_bruecke.nach_analyse); nicht Teil des Befund-Dokuments
    from app.services.log_watch.befund import BefundAbgelehnt, schreibe_befund
    try:
        return await schreibe_befund({"titel": titel, "kern": kern, "fakten": fakten or [],
                                      "folge_von": folge_von or [], "relevanz": relevanz,
                                      "belege": belege, "hypothesen": hypothesen or [],
                                      "hashtags": hashtags or []}, _modell, _schritte, start=_start)
    except BefundAbgelehnt as e:
        return {"abgelehnt": str(e)}


#: höchstens so lange wartet ein Agent auf eine externe Quelle — die Wissensdatenbank stockt
#: zeitweise minutenlang (connectors/aikb.py, Grenze dort 900 s), der Agent hat 480 s Budget
EXTERN_S = 30


async def _extern(was: str, coro) -> dict:
    """Externe Quelle mit Frist; `error` der Quelle heißt hier `fehler` (wie bei allen Werkzeugen)."""
    try:
        r = await asyncio.wait_for(coro, EXTERN_S)
    except asyncio.TimeoutError:
        return {"fehler": f"{was}: keine Antwort nach {EXTERN_S} s"}
    except Exception as e:  # noqa: BLE001 — eine ausgefallene Quelle ist ein benannter Zustand
        return {"fehler": f"{was}: {type(e).__name__}: {str(e)[:200]}"}
    if isinstance(r, dict) and r.get("error"):
        return {**{k: v for k, v in r.items() if k != "error"}, "fehler": f"{was}: {r['error']}"}
    return r


async def get_checkmk_host(hostname: str) -> dict:
    from app.services.checkmk_metrics import host_status
    return await _extern("CheckMK", host_status(hostname))


async def search_tickets(query: str, open_only: bool = False) -> dict:
    from app.core.database import AsyncSessionLocal
    from app.services.ticketing import search_tickets as suche_tickets

    async def _hole():
        async with AsyncSessionLocal() as db:   # ohne Nutzer: nur die globalen Jira-Konnektoren
            r = await suche_tickets(db, None, query, open_only=open_only, max_results=5)
        if not r.get("searched"):               # sonst sähe „nirgends gesucht“ aus wie „kein Ticket“
            return {"error": OHNE_JIRA}
        return r
    return await _extern("Jira", _hole())


#: Grund, warum search_tickets fehlt — der Trichter läuft ohne Nutzer und nimmt keine persönlichen
#: Zugangsdaten (gemessen 07.11.: Jira und ServiceDesk sind nur als persönliche Konnektoren angelegt)
OHNE_JIRA = "kein globaler Jira-/ServiceDesk-Konnektor (nur persönliche) — Tickets nicht durchsuchbar"


async def nicht_verfuegbar() -> dict[str, str]:
    """Werkzeuge, die gerade nicht funktionieren können, mit Grund — der Trichter bietet sie nicht an
    (ein Werkzeug, das nur verweigert, kostet die KI einen Schritt)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.connector import ConnectorConfig
    async with AsyncSessionLocal() as db:
        typen = set((await db.execute(select(ConnectorConfig.type).where(
            ConnectorConfig.enabled.is_(True), ConnectorConfig.owner_user_id.is_(None),
            ConnectorConfig.type.in_(["jira", "jira_sd", "aikb", "checkmk"])))).scalars())
    out = {}
    if not typen & {"jira", "jira_sd"}:
        out["search_tickets"] = OHNE_JIRA
    if "aikb" not in typen:
        out["search_knowledge_base"] = "kein aktiver IT-AIKB-Konnektor"
    if "checkmk" not in typen:
        out["get_checkmk_host"] = "kein CheckMK-Konnektor"
    return out


async def search_knowledge_base(query: str) -> dict:
    from app.core.database import AsyncSessionLocal
    from app.services.connectors.aikb import search_kb

    async def _hole():
        async with AsyncSessionLocal() as db:
            return await search_kb(db, query, size=3)
    return await _extern("Wissensdatenbank", _hole())


def _p(**props) -> dict:
    return {"type": "object", "properties": props, "required": [k for k, v in props.items() if v.pop("_pflicht", False)]}


#: name → (Funktion, Beschreibung, JSON-Schema der Parameter, schreibend)
REGISTRY: dict[str, tuple[Callable[..., Awaitable[dict]], str, dict, bool]] = {
    "lw_suche": (lw_suche,
                 "Relevante Lognachrichten (Graylog, Wazuh, CheckMK, Firewall, Switch) suchen. Liefert "
                 "Treffer (id, zeit, host, relevanz, hashtags, text, gruende) und Facetten (Hashtags des "
                 "Ergebnisses je Art mit Anzahl). " + FILTER_HILFE,
                 _p(filter={"type": "string"}, stunden={"type": "number", "description": "Zeitraum rückwärts"},
                    limit={"type": "integer"}, bedeutung={"type": "string",
                    "description": "optional: Text, zu dem die bedeutungsähnlichsten Nachrichten gesucht werden"}),
                 False),
    "lw_facetten": (lw_facetten,
                    "Nur Anzahl und Facetten (Hashtags je Art mit Anzahl) zu einem Filter — für den "
                    "Trichter: welcher Wert fällt auf? " + FILTER_HILFE,
                    _p(filter={"type": "string"}, stunden={"type": "number"}), False),
    "lw_netzplan": (lw_netzplan,
                    "Netzplan zum Filter: Knoten = Hashtags des Ergebnisses (Host, Thema, Dienst, Regel …), "
                    "Kante = beide an derselben Nachricht, mit Anzahl. Zeigt, was zusammen auftritt — "
                    "keine Ursache. " + FILTER_HILFE,
                    _p(filter={"type": "string"}, stunden={"type": "number"}, knoten={"type": "integer"}),
                    False),
    "lw_gespraech": (lw_gespraech,
                     "Gesprächsprotokolle früherer Analysen (Züge von Worker, KI, Werkzeugen, Befund, Mensch) "
                     "zum Filter, z. B. host:x oder muster:y. " + FILTER_HILFE,
                     _p(filter={"type": "string", "_pflicht": True}, tage={"type": "number"},
                        sitzungen={"type": "integer"}), False),
    "lw_nachricht": (lw_nachricht, "Eine Nachricht vollständig, mit allen Originalfeldern (roh).",
                     _p(id={"type": "string", "_pflicht": True}), False),
    "lw_wissen": (lw_wissen,
                  "Frühere KI-Befunde und Konsolen-Wissen (gelöste Incidents) zu einem Filter oder nach "
                  "Bedeutung — vor jeder Analyse fragen, ob es schon bekannt ist. " + FILTER_HILFE,
                  _p(filter={"type": "string"}, bedeutung={"type": "string"}, stunden={"type": "number"}),
                  False),
    "lw_feld": (lw_feld, "Bedeutung eines Felds, einer Hashtag-Art oder eines Signals nachschlagen, z. B. "
                "regelgruppe, hashtag:decoder, feld:roh, signal:neu; ohne Namen die ganze Liste.",
                _p(name={"type": "string"}), False),
    "lw_feld_anlegen": (lw_feld_anlegen, "Bedeutung eines NEUEN Felds/Hashtags/Signals/Quellfelds anlegen, das lw_feld "
                        "nicht kennt — nur mit Belegen: [{id, zitat}] aus einer Nachricht oder [{url, zitat}] aus einer "
                        "mit web_seite gelesenen Seite; das Zitat muss dort wörtlich stehen (der Code prüft). "
                        "Vorhandenes wird nicht überschrieben. Ein eigenes Tag-Feld (neue Hashtag-Art, z. B. ursache) "
                        "registrierst du hier mit art='hashtag' — erst dann gilt es im Befund. Vorher lw_feld() ohne "
                        "Namen lesen: keine zweite Art für Vorhandenes (Standort = ort). Die Art ist die Eigenschaft "
                        "(komponente), der Wert steht im Tag (komponente:sensecommon). Das Zitat muss die Bedeutung "
                        "stützen, nicht nur irgendwo stehen.",
                        _p(name={"type": "string", "_pflicht": True},
                           art={"type": "string", "enum": ["feld", "hashtag", "signal", "quellfeld"], "_pflicht": True},
                           bedeutung={"type": "string", "_pflicht": True},
                           belege={"type": "array", "_pflicht": True, "items": {"type": "object", "properties": {
                               "id": {"type": "string"}, "url": {"type": "string"}, "zitat": {"type": "string"}},
                               "required": ["zitat"]}},
                           beispiel={"type": "string"}), True),
    "web_suche": (web_suche, "Websuche (SearXNG) — NUR wenn du nicht sicher weißt, was eine Logzeile bedeutet. "
                  "Suche nach Fehlermeldung oder Code (z. B. „ORA-12541 TNS no listener“), nie nach Host, IP oder "
                  "internen Namen (werden entfernt). warum = welche Zeile, was ist unklar. Kontext, kein Beleg.",
                  _p(query={"type": "string", "_pflicht": True}, warum={"type": "string", "_pflicht": True}), False),
    "web_seite": (web_seite, "Eine öffentliche Seite aus web_suche lesen (Text; lange Seiten: Anfang und Ende). "
                  "In Foren steht die Lösung meist im LETZTEN Beitrag — lies das Ende. Keine internen Adressen.",
                  _p(url={"type": "string", "_pflicht": True}), False),
    "lw_frage": (lw_frage,
                 "Rückfrage an den Agenten, der einen Host GERADE analysiert — wie unter Kollegen: was weißt du "
                 "über X, siehst du Y auch? Mit Kontext (belege = IDs deiner Nachrichten). Wartet bis 30 s auf die "
                 "Antwort, sonst später lw_fragen(id). Analysiert niemand den Host, kommt „niemand“ mit Hinweis "
                 "auf lw_gespraech. Antworten sind Kontext, kein Beleg.",
                 _p(host={"type": "string", "_pflicht": True}, frage={"type": "string", "_pflicht": True},
                    belege={"type": "array", "items": {"type": "string"}}), False),
    "lw_fragen": (lw_fragen, "Eine Rückfrage mit Antwort (id) oder deine Rückfragen der letzten 24 h.",
                  _p(id={"type": "string"}, host={"type": "string"}), False),
    "lw_antwort": (lw_antwort,
                   "Eine an dich gestellte Rückfrage beantworten (id aus der Rückfrage) — nur, was du belegen "
                   "kannst; belege = IDs von Nachrichten. Weißt du es nicht, sag das.",
                   _p(id={"type": "string", "_pflicht": True}, antwort={"type": "string", "_pflicht": True},
                      belege={"type": "array", "items": {"type": "string"}}), True),
    "lw_definition": (lw_definition, "Die geltende Relevanz-Definition: Kriterien R1–R8/N0–N6, Objekte, "
                      "Regeln (erste passende entscheidet).", _p(), False),
    "lw_befund": (lw_befund,
                  "Den Befund ablegen — beendet die Analyse. kern: EIN Satz (≤ 300 Zeichen) — was, wo, seit "
                  "wann, wie schlimm. fakten: 2–6 kurze Punkte (je ≤ 300 Zeichen), je ein Wert mit Zeit aus "
                  "einem Zitat. folge_von: IDs früherer Befunde (ki:…) zur selben Lage — nie in den Text. "
                  "Jeder Beleg ist {id, zitat}: die ID einer Nachricht aus lw_suche und ein wörtliches Zitat "
                  "(≥ 8 Zeichen) aus ihrem Text; der Code prüft das. Hypothesen: [{text (ein Satz), status: "
                  "offen|gestuetzt|widerlegt}]. Relevanz 0–3 (3 = dringend). Ursachen nur als Hypothese, nie "
                  "aus zeitlicher Nähe allein. hashtags: die der Belege, dazu eigene Tag-Felder (art:wert, z. B. "
                  "ursache:ansible-rollout) — deren Art muss vorher mit lw_feld_anlegen(art='hashtag') registriert "
                  "sein; sie werden an die Belege gehängt, nicht registrierte kommen mit Grund zurück. "
                  "vi_half/vi_falsch (optional): IDs aus dem Abschnitt Kollektiv-Gedächtnis — vi_half nur für Hinweise, die "
                  "der Befund benutzt (befolgt, zitiert, als Ursache bestätigt), vi_falsch für widerlegte oder irreführende; "
                  "nicht benutzte weglassen.",
                  _p(titel={"type": "string", "_pflicht": True}, kern={"type": "string", "_pflicht": True},
                     fakten={"type": "array", "items": {"type": "string"}},
                     folge_von={"type": "array", "items": {"type": "string"}},
                     relevanz={"type": "integer", "_pflicht": True},
                     belege={"type": "array", "_pflicht": True, "items": {"type": "object", "properties": {
                         "id": {"type": "string"}, "zitat": {"type": "string"}}, "required": ["id", "zitat"]}},
                     hypothesen={"type": "array", "items": {"type": "object", "properties": {
                         "text": {"type": "string"}, "status": {"type": "string",
                                                                "enum": ["offen", "gestuetzt", "widerlegt"]}}}},
                     hashtags={"type": "array", "items": {"type": "string"}},
                     vi_half={"type": "array", "items": {"type": "string"}},
                     vi_falsch={"type": "array", "items": {"type": "string"}}),
                  True),
    "get_checkmk_host": (get_checkmk_host,
                         "Live-Zustand eines Hosts aus CheckMK: Services, die nicht OK sind (mit Statuszeile), "
                         "Anzahl OK, CPU/RAM/Platte der letzten 2 h mit Trend. Prüft, ob ein Problem jetzt "
                         "noch besteht. Kein Beleg für lw_befund (Belege sind Nachrichten der Historie).",
                         _p(hostname={"type": "string", "_pflicht": True}), False),
    "search_tickets": (search_tickets,
                       "Tickets in Jira und ServiceDesk per Freitext (z. B. Hostname, Dienst, Fehlertext). "
                       "Ein offenes Ticket zum Problem heißt: bekannt und behandelt (Kriterium N2) — im Befund "
                       "nennen. Kein Beleg für lw_befund.",
                       _p(query={"type": "string", "_pflicht": True},
                          open_only={"type": "boolean", "description": "nur nicht erledigte Tickets"}), False),
    "search_knowledge_base": (search_knowledge_base,
                              "Interne Wissensdatenbank (Runbooks, Server-Doku, Abhängigkeiten) per Suchbegriff, "
                              "z. B. Hostname oder Dienst. Kontext, kein Beleg für lw_befund.",
                              _p(query={"type": "string", "_pflicht": True}), False),
}


def openai_tools(namen: list[str] | None = None) -> list[dict[str, Any]]:
    """Werkzeuge im OpenAI-Format für generate_with_tools."""
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
            for n, (_, d, p, _) in REGISTRY.items() if namen is None or n in namen]


async def rufe(name: str, argumente: dict, **intern) -> dict:
    """Werkzeug ausführen. Unbekannt oder falsche Argumente → benannter Fehler, kein Abbruch."""
    if name not in REGISTRY:
        return {"fehler": f"unbekanntes Werkzeug {name}"}
    fn, _, schema, _ = REGISTRY[name]
    erlaubt = set(schema["properties"])
    fremd = set(argumente) - erlaubt
    if fremd:
        return {"fehler": f"unbekannte Argumente {sorted(fremd)} (erlaubt: {sorted(erlaubt)})"}
    fehlend = [k for k in schema.get("required", []) if k not in argumente]
    if fehlend:
        return {"fehler": f"Pflichtargumente fehlen: {fehlend}"}
    try:
        return await fn(**argumente, **intern)
    except TypeError as e:
        return {"fehler": f"Argumente passen nicht: {e}"}
