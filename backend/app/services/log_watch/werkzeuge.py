"""Werkzeuge der Log-Überwachung — eine Registry für KI-Trichter und MCP-Server.

Der Agent sieht dasselbe wie der Mensch im Cockpit: dieselbe Suche, dieselben Facetten, dieselbe
Definition. Alle Werkzeuge lesen; einzig `lw_befund` schreibt — die Analyse der KI als Dokument
`quelle:ki`, keine Aktion an einem System (Belege prüft der Code, befund.py).
"""
from __future__ import annotations

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


async def lw_befund(titel: str, befund: str, relevanz: int, belege: list, hypothesen: list | None = None,
                    hashtags: list | None = None, _modell: str = "", _schritte: list | None = None,
                    _start: str = "") -> dict:
    from app.services.log_watch.befund import BefundAbgelehnt, schreibe_befund
    try:
        return await schreibe_befund({"titel": titel, "befund": befund, "relevanz": relevanz,
                                      "belege": belege, "hypothesen": hypothesen or [],
                                      "hashtags": hashtags or []}, _modell, _schritte, start=_start)
    except BefundAbgelehnt as e:
        return {"abgelehnt": str(e)}


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
    "lw_definition": (lw_definition, "Die geltende Relevanz-Definition: Kriterien R1–R8/N0–N6, Objekte, "
                      "Regeln (erste passende entscheidet).", _p(), False),
    "lw_befund": (lw_befund,
                  "Den Befund ablegen — beendet die Analyse. Jeder Beleg ist {id, zitat}: die ID einer "
                  "Nachricht aus lw_suche und ein wörtliches Zitat (≥ 8 Zeichen) aus ihrem Text; der Code "
                  "prüft das. Hypothesen: [{text, status: offen|gestuetzt|widerlegt}]. Relevanz 0–3 "
                  "(3 = dringend). Ursachen nur als Hypothese, nie aus zeitlicher Nähe allein.",
                  _p(titel={"type": "string", "_pflicht": True}, befund={"type": "string", "_pflicht": True},
                     relevanz={"type": "integer", "_pflicht": True},
                     belege={"type": "array", "_pflicht": True, "items": {"type": "object", "properties": {
                         "id": {"type": "string"}, "zitat": {"type": "string"}}, "required": ["id", "zitat"]}},
                     hypothesen={"type": "array", "items": {"type": "object", "properties": {
                         "text": {"type": "string"}, "status": {"type": "string",
                                                                "enum": ["offen", "gestuetzt", "widerlegt"]}}}},
                     hashtags={"type": "array", "items": {"type": "string"}}),
                  True),
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
