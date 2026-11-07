"""Jede Nachricht der Historie ist ein Objekt (Nutzer 07.11.: „jeder OpenSearch-Eintrag ist ein Objekt, das
nach der Regel der objektorientierten Programmierung verändert werden kann — die API muss dahin gehen“).

Vorher änderten sechs Stellen dieselben Dokumente mit je eigenem Skript (Befund-Belege, Wiederholungen,
Gesehenes, Incidents, R7, Vektor) — kein Mensch konnte ein Urteil ändern, keine Änderung hatte Urheber oder
Grund. Jetzt gilt wie bei einer Klasse:

  feste Felder      zeit, quelle, host, text, roh, schluessel … — der Fakt aus der Quelle, nie geändert
  änderbare Felder  urteil, relevanz, kriterien, gruende, hashtags, ai_* — NUR über die Methoden hier
  Methoden          verknuepfe (befund:/incident:), markiere (Hashtags hinzu/weg), urteile (Mensch/KI),
                    neu_bewerten (Vokabular und Regelwerk der geltenden Definition erneut anwenden)
  Geschichte        jede Änderung hängt einen Eintrag an `aenderungen` (zeit, wer, was, grund) und setzt
                    `geaendert` — die Begründungskette bleibt am Objekt

Vorrang der Urheber (historie.HERKUNFT, Plan): Mensch vor KI vor Regel — eine Regel überschreibt kein Urteil
der KI, die KI keines eines Menschen; die Ablehnung nennt den Grund.

Jede Methode wirkt auf EINE Nachricht (ids) oder auf das Ergebnis eines Filters — Objekte sind
Suchergebnisse (filter.py), dieselbe Sprache wie überall. Der Vektor (historie.nachbetten) ist Technik, kein
Inhalt, und bleibt außerhalb.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

FEST = ("zeit", "quelle", "host", "host_bekannt", "schluessel", "text", "roh", "level", "gruppe_anzahl",
        "raster_v", "vektor")
AENDERBAR = ("urteil", "relevanz", "kriterien", "gruende", "hashtags", "herkunft", "definition_v",
             "ai_relevanz", "ai_hashtags", "ai_befund", "ai_provenance")
#: Urheber und ihr Rang (höher schlägt niedriger)
RANG = {"regel": 0, "ki": 1, "mensch": 2}
#: Hashtags, die feste Felder spiegeln — sie ändern hieße den Fakt ändern (Identität)
FESTE_TAG_ARTEN = ("quelle", "host")
#: Verknüpfungen zu anderen Objekten — der Hashtag ist die Kante (ein Name für ein Ding)
VERKNUEPFUNG = ("befund", "incident")
MAPPING_NEU = {"aenderungen": {"type": "object", "enabled": False}, "geaendert": {"type": "date"}}


class NachrichtAbgelehnt(ValueError):
    """Eine Änderung ist nicht erlaubt — mit Grund (fest, Vorrang, unbekannt)."""


def _eintrag(wer: str, was: str, grund: str, jetzt: datetime) -> dict:
    if wer not in RANG:
        raise NachrichtAbgelehnt(f"Urheber {wer!r} unbekannt (erlaubt: {', '.join(RANG)})")
    if not (grund or "").strip():
        raise NachrichtAbgelehnt("Grund ist Pflicht — jede Änderung braucht einen zureichenden Grund")
    return {"zeit": jetzt.astimezone(timezone.utc).isoformat(), "wer": wer, "was": was, "grund": grund.strip()[:500]}


def pruefe_hashtags(hinzu: list[str], weg: list[str]) -> None:
    """Feste Hashtags (quelle:, host:) sind nicht änderbar; Form art:wert ist Pflicht. Rein."""
    for t in list(hinzu) + list(weg):
        art, _, wert = (t or "").partition(":")
        if not art or not wert:
            raise NachrichtAbgelehnt(f"Hashtag {t!r} hat nicht die Form art:wert")
        if art in FESTE_TAG_ARTEN:
            raise NachrichtAbgelehnt(f"{t}: {art}: spiegelt ein festes Feld und ist nicht änderbar")


# ── Ein Schreibweg: Painless je Methode, immer mit Geschichte ─────────────────

_GESCHICHTE = ("if (ctx._source.aenderungen == null) { ctx._source.aenderungen = []; }"
               " ctx._source.aenderungen.add(params.e); ctx._source.geaendert = params.e.zeit;")

#: hängt eine Verknüpfung an; noop, wenn sie schon da ist (idempotent — zählt nicht doppelt)
SKRIPT_VERKNUEPFE = (
    "if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
    " if (ctx._source.hashtags.contains(params.t)) { ctx.op = 'noop'; } else {"
    " ctx._source.hashtags.add(params.t);"
    " if (params.r != null) { ctx._source.ai_relevanz = params.r; }"
    " if (params.p != null && ctx._source.ai_provenance == null) { ctx._source.ai_provenance = params.p; }"
    " " + _GESCHICHTE + " }")

SKRIPT_MARKIERE = (
    "if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
    " boolean neu = false;"
    " for (def t : params.weg) { if (ctx._source.hashtags.remove(t)) { neu = true; } }"
    " for (def t : params.hinzu) { if (!ctx._source.hashtags.contains(t)) { ctx._source.hashtags.add(t); neu = true; } }"
    " if (!neu) { ctx.op = 'noop'; } else { " + _GESCHICHTE + " }")

#: Urteil setzen — nur, wenn der Urheber nicht niedriger steht als der bisherige (Vorrang)
SKRIPT_URTEILE = (
    "int alt = params.rang.containsKey(ctx._source.herkunft) ? params.rang[ctx._source.herkunft] : 0;"
    " if (params.rang[params.e.wer] < alt) { ctx.op = 'noop'; } else {"
    " ctx._source.urteil = params.urteil; ctx._source.relevanz = params.relevanz;"
    " ctx._source.kriterien = params.kriterien; ctx._source.herkunft = params.e.wer;"
    " if (ctx._source.gruende == null) { ctx._source.gruende = []; } ctx._source.gruende.add(params.e.grund);"
    " " + _GESCHICHTE + " }")


async def stelle_bereit() -> None:
    """Felder der Geschichte im Mapping aller bestehenden Tagesindizes (die Vorlage wirkt nur auf neue)."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    await get_opensearch().indices.put_mapping(index=f"{PRAEFIX}*", body={"properties": MAPPING_NEU})


async def _auf_ids(items: list[tuple[str, str]], skript: str, params: dict) -> dict:
    """Skript auf (Index, ID)-Paare; Ergebnis je ID: geaendert | unveraendert | fehler."""
    from app.core.opensearch import get_opensearch
    if not items:
        return {}
    body: list[dict] = []
    for idx, i in items:
        body += [{"update": {"_index": idx, "_id": i, "retry_on_conflict": 3}},
                 {"script": {"lang": "painless", "source": skript, "params": params}}]
    res = await get_opensearch().bulk(body=body, refresh=True)
    out = {}
    for (_idx, i), it in zip(items, res.get("items") or []):
        r = it.get("update") or {}
        out[i] = "fehler" if r.get("error") else ("geaendert" if r.get("result") == "updated" else "unveraendert")
    return out


async def _auf_filter(abfrage: dict, skript: str, params: dict) -> int:
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    r = await get_opensearch().update_by_query(index=f"{PRAEFIX}*", conflicts="proceed", refresh=True, body={
        "query": abfrage, "script": {"lang": "painless", "source": skript, "params": params}})
    return int(r.get("updated") or 0)


async def _index_von(ids: list[str]) -> list[tuple[str, str]]:
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    if not ids:
        return []
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": len(ids), "_source": False, "query": {"ids": {"values": list(ids)}}})
    return [(h["_index"], h["_id"]) for h in r["hits"]["hits"]]


def _abfrage(filtertext: str, objekte: dict | None, zusatz: dict | None = None) -> dict:
    from app.services.log_watch import filter as flt
    teile = [flt.opensearch(filtertext, objekte or {})] if (filtertext or "").strip() else []
    if zusatz:
        teile.append(zusatz)
    if not teile:
        raise NachrichtAbgelehnt("leerer Filter — eine Änderung an der ganzen Historie ist nicht erlaubt")
    return {"bool": {"filter": teile, "must_not": [{"terms": {"quelle": ["ki", "konsole"]}}]}}


# ── Methoden ──────────────────────────────────────────────────────────────────

async def verknuepfe(ziel: str, wer: str, grund: str, *, ids: list[str] | None = None,
                     items: list[tuple[str, str]] | None = None, abfrage: dict | None = None,
                     ai_relevanz=None, ai_provenance: dict | None = None,
                     jetzt: datetime | None = None) -> dict | int:
    """Nachricht(en) mit einem Objekt verknüpfen (`befund:<id>`, `incident:<id>`). Ein Weg für Belege,
    Wiederholungen, Gesehenes und Incidents. ids/items → Ergebnis je ID; abfrage → Zahl geänderter."""
    art = ziel.partition(":")[0]
    if art not in VERKNUEPFUNG:
        raise NachrichtAbgelehnt(f"Verknüpfung {ziel!r}: nur {', '.join(VERKNUEPFUNG)}")
    params = {"t": ziel, "r": ai_relevanz, "p": ai_provenance,
              "e": _eintrag(wer, f"verknüpft mit {ziel}", grund, jetzt or datetime.now(timezone.utc))}
    if abfrage is not None:
        return await _auf_filter({"bool": {"filter": [abfrage], "must_not": [{"term": {"hashtags": ziel}}]}},
                                 SKRIPT_VERKNUEPFE, params)
    return await _auf_ids(items if items is not None else await _index_von(ids or []), SKRIPT_VERKNUEPFE, params)


async def markiere(hinzu: list[str], weg: list[str], wer: str, grund: str, *, ids: list[str] | None = None,
                   filtertext: str = "", objekte: dict | None = None, jetzt: datetime | None = None) -> dict | int:
    """Hashtags hinzufügen/entfernen (nicht quelle:/host:)."""
    pruefe_hashtags(hinzu, weg)
    was = " ".join([f"+{t}" for t in hinzu] + [f"-{t}" for t in weg])
    params = {"hinzu": list(hinzu), "weg": list(weg), "e": _eintrag(wer, was, grund, jetzt or datetime.now(timezone.utc))}
    if ids is not None:
        return await _auf_ids(await _index_von(ids), SKRIPT_MARKIERE, params)
    return await _auf_filter(_abfrage(filtertext, objekte), SKRIPT_MARKIERE, params)


async def urteile(urteil: str, relevanz: int, kriterien: list[str], wer: str, grund: str, *,
                  ids: list[str] | None = None, filtertext: str = "", objekte: dict | None = None,
                  jetzt: datetime | None = None) -> dict | int:
    """Urteil setzen (Mensch oder KI) — mit Vorrang: niedrigerer Urheber ändert nichts (unveraendert)."""
    from app.services.log_watch.definition import KRITERIEN, NICHT_RELEVANT, RELEVANT
    if urteil not in (RELEVANT, NICHT_RELEVANT):
        raise NachrichtAbgelehnt(f"Urteil {urteil!r}: nur {RELEVANT} oder {NICHT_RELEVANT}")
    if not isinstance(relevanz, int) or not 0 <= relevanz <= 3:
        raise NachrichtAbgelehnt("Relevanz muss 0–3 sein")
    gueltig = {k.id for k in KRITERIEN}
    if not kriterien or set(kriterien) - gueltig:
        raise NachrichtAbgelehnt(f"Kriterien {kriterien}: mindestens eines, nur aus {', '.join(sorted(gueltig))}")
    if (urteil == RELEVANT) != (kriterien[0].startswith("R")):
        raise NachrichtAbgelehnt("Das erste Kriterium muss zum Urteil passen (R… für relevant, N… für nicht relevant)")
    params = {"urteil": urteil, "relevanz": relevanz, "kriterien": list(kriterien), "rang": RANG,
              "e": _eintrag(wer, f"Urteil {urteil} ({', '.join(kriterien)}), Relevanz {relevanz}", grund,
                            jetzt or datetime.now(timezone.utc))}
    if ids is not None:
        return await _auf_ids(await _index_von(ids), SKRIPT_URTEILE, params)
    return await _auf_filter(_abfrage(filtertext, objekte), SKRIPT_URTEILE, params)


def neu_bewertet(doc: dict, regeln: list, version: int) -> dict | None:
    """Eine gespeicherte Nachricht mit dem geltenden Vokabular und Regelwerk erneut beurteilen. Rein — testbar.
    Themen kommen neu aus dem Text; `signal:schaden`/`frist` fallen nur weg, wenn ein Thema ihr einziger
    Grund war. Das Urteil ändert sich nur, wenn es von der Regel stammt (Vorrang). None = nichts ändert sich."""
    from app.services.log_watch.definition import NICHT_RELEVANT, RELEVANT
    from app.services.log_watch.raster import THEMEN_R5, THEMEN_R8
    from app.services.log_watch.regelwerk import FORWARD, bewerte
    from app.services.log_watch.vokabular import themen

    tags = list(doc.get("hashtags") or [])
    gruende = list(doc.get("gruende") or [])
    neu_th = {t.id: t for t in themen(doc.get("text") or "")}
    alt_th = {t.split(":", 1)[1] for t in tags if t.startswith("thema:")}
    if doc.get("quelle") == "checkmk":
        neu_th_ids = alt_th          # CheckMK-Themen kommen auch aus dem Dienstnamen (dienst_thema) — bleiben
    else:
        neu_th_ids = set(neu_th)
    weg_th, hinzu_th = alt_th - neu_th_ids, neu_th_ids - alt_th
    tags = [t for t in tags if not (t.startswith("thema:") and t.split(":", 1)[1] in weg_th)]
    tags += [f"thema:{t}" for t in sorted(hinzu_th)]
    from app.services.log_watch.vokabular import THEMEN
    text_von = {t.id: t.text for t in THEMEN}
    for th in weg_th:
        gruende = [g for g in gruende if g not in (f"Thema „{text_von.get(th, th)}“ (Schaden an Ressourcen)",
                                                   f"Thema „{text_von.get(th, th)}“ (ablaufende Frist)")]
    # Signal bleibt, wenn ein anderer Grund es trägt (Kernelmeldung, CheckMK-Dienst, ein weiteres Thema)
    th_jetzt = {t.split(":", 1)[1] for t in tags if t.startswith("thema:")}
    if "signal:schaden" in tags and not (th_jetzt & THEMEN_R5) and doc.get("quelle") != "checkmk" \
            and not any("Kernelmeldung" in g or "Hardware" in g for g in gruende):
        tags.remove("signal:schaden")
    if "signal:frist" in tags and not (th_jetzt & THEMEN_R8) and doc.get("quelle") != "checkmk":
        tags.remove("signal:frist")
    for th in sorted(hinzu_th):
        if th in THEMEN_R5 and "signal:schaden" not in tags:
            tags.append("signal:schaden")
            gruende.append(f"Thema „{text_von[th]}“ (Schaden an Ressourcen)")
    aenderung = {}
    if sorted(tags) != sorted(doc.get("hashtags") or []):
        aenderung["hashtags"] = tags
        aenderung["gruende"] = gruende
    if (doc.get("herkunft") or "regel") == "regel":
        e = bewerte(regeln, set(tags), {"level": doc.get("level")} if doc.get("level") is not None else {})
        kriterien = list(e.kriterien)
        if e.aktion == FORWARD:
            urteil, rel = RELEVANT, 3 if "signal:dringend" in tags else 2
            if "R7" in (doc.get("kriterien") or []):          # Zusammentreffen bleibt (historie.markiere_r7)
                rel, kriterien = 3, kriterien + ["R7"]
        else:
            urteil, rel = NICHT_RELEVANT, e.stufe_block
        if (urteil, rel, kriterien) != (doc.get("urteil"), doc.get("relevanz"), doc.get("kriterien")):
            aenderung.update(urteil=urteil, relevanz=rel, kriterien=kriterien, definition_v=version)
    return aenderung or None


async def neu_bewerten(filtertext: str, wer: str, grund: str, *, objekte: dict | None = None,
                       stunden: float | None = None, grenze: int = 5000, jetzt: datetime | None = None) -> dict:
    """Vokabular und Regelwerk der geltenden Definition auf gespeicherte Nachrichten anwenden."""
    from datetime import timedelta

    from app.core.database import AsyncSessionLocal
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.definition import aktuelle
    from app.services.log_watch.historie import PRAEFIX
    jetzt = jetzt or datetime.now(timezone.utc)
    e = _eintrag(wer, "neu bewertet", grund, jetzt)
    async with AsyncSessionLocal() as db:
        d = await aktuelle(db)
    zusatz = {"range": {"zeit": {"gte": (jetzt - timedelta(hours=stunden)).isoformat()}}} if stunden else None
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": grenze, "track_total_hits": True, "query": _abfrage(filtertext, objekte or d.objekte, zusatz),
        "_source": ["text", "quelle", "hashtags", "gruende", "urteil", "relevanz", "kriterien", "herkunft", "level"]})
    body: list[dict] = []
    geaendert = 0
    for h in r["hits"]["hits"]:
        a = neu_bewertet(h["_source"], d.regeln, d.version)
        if not a:
            continue
        was = ", ".join(f"{k}" for k in a)
        body += [{"update": {"_index": h["_index"], "_id": h["_id"], "retry_on_conflict": 3}},
                 {"script": {"lang": "painless", "params": {"a": a, "e": {**e, "was": f"neu bewertet: {was}"}},
                             "source": "for (def k : params.a.keySet()) { ctx._source[k] = params.a[k]; } " + _GESCHICHTE}}]
        geaendert += 1
    if body:
        await get_opensearch().bulk(body=body, refresh=True)
    return {"gefunden": r["hits"]["total"]["value"], "geprueft": len(r["hits"]["hits"]), "geaendert": geaendert,
            "definition_v": d.version}


async def lies(id_: str) -> dict | None:
    """Das Objekt: alle Felder, getrennt nach fest/änderbar, mit Verknüpfungen und Geschichte."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    r = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 1, "query": {"ids": {"values": [id_]}}, "_source": {"excludes": ["vektor"]}})
    if not r["hits"]["hits"]:
        return None
    h = r["hits"]["hits"][0]
    s = h["_source"]
    tags = s.get("hashtags") or []
    return {"id": h["_id"], "index": h["_index"],
            "fest": {k: s[k] for k in FEST if k in s},
            "aenderbar": {k: s[k] for k in AENDERBAR if k in s},
            "verknuepft": {a: [t.split(":", 1)[1] for t in tags if t.startswith(a + ":")] for a in VERKNUEPFUNG},
            "aenderungen": s.get("aenderungen") or [], "geaendert": s.get("geaendert")}
