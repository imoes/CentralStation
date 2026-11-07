"""Bekannte Fehler: was die KI schon untersucht hat, untersucht sie nicht noch einmal.

Nutzer, 07.11.: „Wenn die KI bereits ein Problem untersucht hat und derselbe Fehler wieder
auftaucht, dann muss die KI nichts mehr machen.“

Derselbe Fehler = derselbe Fehler-Schlüssel (`fehler()`), gebildet aus dem Gruppenschlüssel, den das
Raster an jede Nachricht schreibt (`schluessel`):
  - `muster:<drain>` (Graylog, Switch) — über alle Hosts: dasselbe Muster ist derselbe Fehler
    (wie die Fälle des Trichters, trichter.faelle);
  - `<host>|regel:<id>` (Wazuh), `<host>|dienst:<service>` (CheckMK), `<host>|bedrohung:<x>`
    (Firewall) — je Host.

Ein bekannter Fehler ist ein Objekt wie jedes andere (Nutzer, 07.11.: „jedes Objekt ist ein
Ergebnis-Dokument einer Suche und Filter, mit Feldern wie Host und Tags“): er trägt seinen `filter`
in der einen Filtersprache (`muster:x` bzw. `host:h dienst:"Filesystem /"`), dazu `host` und
`hashtags` wie eine Nachricht. Der Filter IST die Suche nach dem Fehler — ein Klick führt in genau
diese Suche. Verglichen wird im Trichter über den Schlüssel (schnell); dass Schlüssel und Filter
dieselben Nachrichten treffen, belegt ein Test — gemessen 07.11.: an allen 7 677 Nachrichten der
Historie steht der Gruppenschlüssel auch als Hashtag.

Gelernt wird nach jedem Befund (`lerne`): jeder Schlüssel, den die Analyse gesehen hat (Lage des
Falls und zitierte Belege), zeigt danach auf diesen Befund. Taucht der Fehler wieder auf, hängt der
Code die Nachricht an den Befund (`haenge_an`: Hashtag `befund:<id>`, `ai_relevanz` des Befunds,
`ai_provenance.bekannt`) und zählt die Wiederholung — ohne KI. Der Filter `befund:<id>` zeigt so
Belege und Wiederholungen.

Ausnahme, die der Plan schon festlegt (Vorrang N2: „erst wieder relevant, wenn ein R hinzukommt,
das beim Behandeln noch nicht vorlag“): trägt die neue Nachricht ein Kriterium R1–R8 oder
`signal:dringend`, das der Fehler beim Befund nicht hatte, geht sie an die KI, mit dem Grund
`neu_seit_befund`. Ein Mensch kann einen bekannten Fehler vergessen lassen (`vergiss`, Status
`widerlegt`, mit Grund): dann analysiert die KI beim nächsten Auftreten neu, und ihr neuer Befund
macht den Eintrag wieder aktiv.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

INDEX = "cs-log-bekannt"
#: Zustände eines Eintrags — erschöpfend
STATUS = ("aktiv", "widerlegt")
MAPPING = {
    "dynamic": "strict",
    "properties": {
        "fehler": {"type": "keyword"},              # Fehler-Schlüssel (fehler())
        "filter": {"type": "keyword"},              # derselbe Fehler als Suche (Filtersprache)
        "hashtags": {"type": "keyword"},            # Hashtags seiner Nachrichten, art:wert
        "schluessel": {"type": "keyword"},          # Gruppenschlüssel des Rasters
        "host": {"type": "keyword"},                # leer bei muster: (über alle Hosts)
        "befund": {"type": "keyword"},              # ki:<id> — der Befund, der den Fehler kennt
        "titel": {"type": "text"},
        "relevanz": {"type": "byte"},               # Relevanz laut Befund
        "merkmale": {"type": "keyword"},            # Kriterien R* und „dringend“, die der Befund sah
        "status": {"type": "keyword"},              # aktiv | widerlegt
        "herkunft": {"type": "keyword"},            # ki | mensch (wer den Status setzte)
        "erstmals": {"type": "date"},
        "gelernt": {"type": "date"},                # Zeit des letzten Befunds dazu
        "zuletzt": {"type": "date"},                # letzte Wiederholung
        "wiederholungen": {"type": "integer"},      # ohne KI angehängte Nachrichten
        "hosts": {"type": "keyword"},               # Hosts, auf denen er auftrat
        "grund": {"type": "text"},                  # Grund des Menschen beim Vergessen
        "geaendert_von": {"type": "keyword"},
    },
}


def fehler(n: dict) -> str | None:
    """Fehler-Schlüssel einer Nachricht; None = ohne Gruppenschlüssel (geht immer an die KI)."""
    s = n.get("schluessel") or ""
    if not s:
        return None
    return s if s.startswith("muster:") else f"{n.get('host') or ''}|{s}"


def merkmale(n: dict) -> set[str]:
    """Was eine erneute Analyse begründet: Relevanz-Kriterien R* und das Signal „dringend“."""
    m = {k for k in n.get("kriterien") or [] if k.startswith("R")}
    if "signal:dringend" in (n.get("hashtags") or []):
        m.add("dringend")
    return m


def _tag_filter(tag: str) -> str:
    """Hashtag → Filterausdruck; Werte mit Leerzeichen, Klammern oder `*` am Ende (sonst Präfix)
    in Anführungszeichen."""
    art, _, wert = tag.partition(":")
    if '"' in wert:
        raise ValueError(f"Hashtag {tag!r} ist in der Filtersprache nicht schreibbar (Anführungszeichen)")
    return f'{art}:"{wert}"' if any(c in wert for c in ' ()\t') or wert.endswith("*") or not wert else tag


def filter_von(schluessel: str, host: str) -> str:
    """Der Fehler als Suche: muster über alle Hosts, sonst Host und Gruppenschlüssel."""
    if schluessel.startswith("muster:"):
        return _tag_filter(schluessel)
    return f"{_tag_filter('host:' + host)} {_tag_filter(schluessel)}"


#: Hashtags, die am Objekt nichts sagen: die Verknüpfung zu Befunden steht in `befund`, Signale
#: wechseln je Nachricht
_OHNE = ("befund:", "signal:")
HASHTAGS_MAX = 60


def doc_id(fehler_schluessel: str) -> str:
    return hashlib.sha1(fehler_schluessel.encode()).hexdigest()


def teile(offen: list[dict], bekannt: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Offene Nachrichten → (an die KI, an einen Befund hängen). Rein — testbar.
    Eine Nachricht mit neuem Merkmal geht an die KI und trägt `neu_seit_befund`."""
    ki, haengen = [], []
    for n in offen:
        f = fehler(n)
        e = bekannt.get(f) if f else None
        if e is None or e.get("status") != "aktiv":
            ki.append(n)
            continue
        neu = merkmale(n) - set(e.get("merkmale") or [])
        if neu:
            ki.append({**n, "neu_seit_befund": sorted(neu), "bekannt_als": e.get("befund")})
            continue
        haengen.append({"id": n.get("_id"), "index": n.get("_index"), "fehler": f, "host": n.get("host") or "",
                        "zeit": n.get("zeit"), "befund": e["befund"], "relevanz": e.get("relevanz")})
    return ki, haengen


def eintraege(befund: str, titel: str, relevanz: int, gesehen: list[dict], alt: dict[str, dict],
              jetzt: datetime) -> list[dict]:
    """Einträge nach einem Befund: je Fehler-Schlüssel der gesehenen Nachrichten. Rein — testbar.
    Ein vorhandener Eintrag zeigt danach auf den neuen Befund (Folge-Befund), behält Zähler und
    erstes Auftreten und vereinigt die Merkmale — ein Merkmal, das einmal gesehen wurde, löst nicht
    erneut aus."""
    je: dict[str, dict] = {}
    for n in gesehen:
        f = fehler(n)
        if not f:
            continue
        e = je.setdefault(f, {"fehler": f, "schluessel": n["schluessel"],
                              "host": "" if n["schluessel"].startswith("muster:") else (n.get("host") or ""),
                              "merkmale": set(), "hosts": set(), "hashtags": set()})
        e["merkmale"] |= merkmale(n)
        e["hashtags"] |= {t for t in n.get("hashtags") or [] if not t.startswith(_OHNE)}
        if n.get("host"):
            e["hosts"].add(n["host"])
    z = jetzt.astimezone(timezone.utc).isoformat()
    out = []
    for f, e in je.items():
        a = alt.get(f) or {}
        try:
            flt = filter_von(e["schluessel"], e["host"])
        except ValueError:
            flt = ""                                   # benannt leer; der Schlüssel trägt weiter
        tags = sorted(e["hashtags"] | set(a.get("hashtags") or []))
        # Kern-Hashtags (Gruppenschlüssel, Hosts) zuerst, damit die Kappung nie sie trifft
        kern = [t for t in tags if t == e["schluessel"] or t.startswith("host:")]
        out.append({**e, "filter": flt, "hashtags": (kern + [t for t in tags if t not in kern])[:HASHTAGS_MAX],
                    "befund": befund, "titel": titel[:300], "relevanz": relevanz,
                    "merkmale": sorted(e["merkmale"] | set(a.get("merkmale") or [])),
                    "hosts": sorted(e["hosts"] | set(a.get("hosts") or []))[:200],
                    "status": "aktiv", "herkunft": "ki", "erstmals": a.get("erstmals") or z, "gelernt": z,
                    "zuletzt": a.get("zuletzt") or z, "wiederholungen": a.get("wiederholungen") or 0,
                    **({"grund": a["grund"]} if a.get("grund") else {})})
    return out


async def stelle_bereit() -> None:
    from app.core.opensearch import get_opensearch
    os_client = get_opensearch()
    if not await os_client.indices.exists(index=INDEX):
        await os_client.indices.create(index=INDEX, body={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0}, "mappings": MAPPING})


async def lade(fehler_schluessel: list[str]) -> dict[str, dict]:
    """Einträge zu Fehler-Schlüsseln (fehlender Index = nichts bekannt)."""
    from app.core.opensearch import get_opensearch
    keys = sorted({f for f in fehler_schluessel if f})
    if not keys:
        return {}
    try:
        res = await get_opensearch().mget(index=INDEX, body={"ids": [doc_id(f) for f in keys]})
    except Exception as e:  # noqa: BLE001
        if "index_not_found" in str(e):
            return {}
        raise
    return {d["_source"]["fehler"]: {**d["_source"], "id": d["_id"]} for d in res["docs"] if d.get("found")}


def gesehen_aus(docs: dict[str, dict], beleg_ids: list[str], lage_ids: list[str], befund: str) -> list[dict]:
    """Was eine Analyse gesehen hat. Rein — testbar. Belege immer; aus der Lage nur Nachrichten, die
    noch an keinem anderen Befund hängen (sonst zöge ein Host-Befund fremde Fehler an sich).
    Befunde und Konsolen-Wissen sind keine Fehler."""
    eigen = f"befund:{befund.split(':', 1)[-1]}"
    out = []
    for i in dict.fromkeys([*beleg_ids, *lage_ids]):
        d = docs.get(i)
        if d is None or d.get("quelle") in ("ki", "konsole"):
            continue
        fremd = any(t.startswith("befund:") and t != eigen for t in d.get("hashtags") or [])
        if i in beleg_ids or not fremd:
            out.append(d)
    return out


async def lerne(befund: str, titel: str, relevanz, beleg_ids: list[str], lage_ids: list[str] | None = None,
                jetzt: datetime | None = None) -> int:
    """Nach einem Befund: die gesehenen Fehler als bekannt ablegen. Rückgabe: geschriebene Einträge."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    beleg_ids = [str(i) for i in beleg_ids or [] if i]
    lage_ids = [str(i) for i in lage_ids or [] if i]
    ids = sorted(set(beleg_ids) | set(lage_ids))
    if not ids:
        return 0
    try:
        relevanz = int(relevanz)
    except (TypeError, ValueError):
        relevanz = 0
    os_client = get_opensearch()
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": len(ids), "query": {"ids": {"values": ids}},
        "_source": ["schluessel", "host", "kriterien", "hashtags", "quelle"]})
    gesehen = gesehen_aus({h["_id"]: h["_source"] for h in res["hits"]["hits"]}, beleg_ids, lage_ids, befund)
    alt = await lade([fehler(n) for n in gesehen])
    neu = eintraege(befund, titel, relevanz, gesehen, alt, jetzt or datetime.now(timezone.utc))
    if not neu:
        return 0
    await stelle_bereit()
    body: list[dict] = []
    for e in neu:
        body += [{"index": {"_index": INDEX, "_id": doc_id(e["fehler"])}}, e]
    await os_client.bulk(body=body, refresh=True)
    return len(neu)


#: hängt eine Nachricht an den Befund — nur einmal (noop, wenn sie ihn schon trägt), damit zwei
#: Agenten, die dieselbe Nachricht sehen, die Wiederholung nicht doppelt zählen
_ANHAENGEN = ("if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
              " if (ctx._source.hashtags.contains(params.b)) { ctx.op = 'noop'; } else {"
              " ctx._source.hashtags.add(params.b); ctx._source.ai_relevanz = params.r;"
              " ctx._source.ai_provenance = params.p; }")


async def haenge_an(haengen: list[dict], jetzt: datetime | None = None) -> dict:
    """Wiederholungen bekannter Fehler an ihren Befund hängen, ohne KI. Rückgabe {angehaengt, fehler}."""
    from app.core.opensearch import get_opensearch
    if not haengen:
        return {"angehaengt": 0}
    z = (jetzt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    os_client = get_opensearch()
    body: list[dict] = []
    for h in haengen:
        kurz = h["befund"].split(":", 1)[1]
        body += [{"update": {"_index": h["index"], "_id": h["id"], "retry_on_conflict": 3}},
                 {"script": {"lang": "painless", "source": _ANHAENGEN, "params": {
                     "b": f"befund:{kurz}", "r": h.get("relevanz"),
                     "p": {"bekannt": {"befund": h["befund"], "fehler": h["fehler"], "zeit": z}}}}}]
    res = await os_client.bulk(body=body, refresh=True)
    gezaehlt: dict[str, dict] = {}
    fehler_n = 0
    for h, item in zip(haengen, res.get("items") or []):
        r = item.get("update") or {}
        if r.get("error"):
            fehler_n += 1
        elif r.get("result") == "updated":
            g = gezaehlt.setdefault(h["fehler"], {"n": 0, "zuletzt": "", "hosts": set()})
            g["n"] += 1
            g["zuletzt"] = max(g["zuletzt"], str(h.get("zeit") or ""))
            if h["host"]:
                g["hosts"].add(h["host"])
    if gezaehlt:
        body = []
        for f, g in gezaehlt.items():
            body += [{"update": {"_index": INDEX, "_id": doc_id(f), "retry_on_conflict": 5}},
                     {"script": {"lang": "painless", "params": {"n": g["n"], "z": g["zuletzt"] or z,
                                                                 "h": sorted(g["hosts"])},
                                 "source": "ctx._source.wiederholungen = (ctx._source.wiederholungen ?: 0) + params.n;"
                                           " if (params.z.compareTo(ctx._source.zuletzt ?: '') > 0)"
                                           " { ctx._source.zuletzt = params.z; }"
                                           " for (def x : params.h) { if (!ctx._source.hosts.contains(x)"
                                           " && ctx._source.hosts.size() < 200) { ctx._source.hosts.add(x); } }"}}]
        await os_client.bulk(body=body, refresh=True)
    return {"angehaengt": sum(g["n"] for g in gezaehlt.values()), "fehler": fehler_n}


#: hängt eine gesehene Nachricht an den Befund — ai_provenance nur, wenn noch keine da ist
_GESEHEN = ("if (ctx._source.hashtags == null) { ctx._source.hashtags = []; }"
            " if (ctx._source.hashtags.contains(params.b)) { ctx.op = 'noop'; } else {"
            " ctx._source.hashtags.add(params.b); ctx._source.ai_relevanz = params.r;"
            " if (ctx._source.ai_provenance == null) { ctx._source.ai_provenance = params.p; } }")


def gesehen_abfrage(start: str, seit: datetime, bis: datetime) -> dict:
    """Was eine Analyse gesehen hat — dieselbe Definition wie trichter.offene: alles, was ihr Startfilter
    im Prüffenster bis zu ihrem Zeitpunkt traf; noch an keinem Befund, keine Befunde selbst."""
    from app.services.log_watch import filter as flt
    return {"bool": {"filter": [flt.opensearch(start),
                                {"range": {"zeit": {"gte": seit.isoformat(), "lte": bis.isoformat()}}}],
                     "must_not": [{"prefix": {"hashtags": "befund:"}}, {"terms": {"quelle": ["ki", "konsole"]}}]}}


async def markiere_gesehen(befund: str, relevanz, start: str, bis: datetime, stunden: float) -> int:
    """Nach einem Befund: jede Nachricht, die die Analyse gesehen hat, trägt `befund:<id>` — wie Belege und
    Wiederholungen. Vorher trugen nur die 2–3 zitierten den Hashtag; die übrigen galten im Netzplan als
    unbearbeitet, obwohl die Prüfung (trichter.offene) sie als gesehen führte — zwei Definitionen für
    dasselbe (gemessen 07.11.: alle 22 Knoten des Fehler-Netzplans rot)."""
    from datetime import timedelta

    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    if not start:
        return 0
    try:
        relevanz = int(relevanz)
    except (TypeError, ValueError):
        relevanz = None
    seit = bis - timedelta(hours=stunden)
    r = await get_opensearch().update_by_query(
        index=f"{PRAEFIX}*", conflicts="proceed", refresh=True, body={
            "query": gesehen_abfrage(start, seit, bis),
            "script": {"lang": "painless", "source": _GESEHEN, "params": {
                "b": f"befund:{befund.split(':', 1)[-1]}", "r": relevanz,
                "p": {"gesehen": {"befund": befund, "start": start, "zeit": bis.astimezone(timezone.utc).isoformat()}}}}})
    return int(r.get("updated") or 0)


async def gesehen_nachtragen(tage: float = 7, stunden: float = 2.0 + 1 / 6) -> dict:
    """Einmalig: für die Befunde der letzten Tage markieren, was sie gesehen haben (Prüffenster STUNDEN + TAKT)."""
    from datetime import timedelta

    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    res = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 5000, "sort": [{"zeit": "asc"}], "_source": ["zeit", "relevanz", "ai_provenance.start"],
        "query": {"bool": {"filter": [{"term": {"quelle": "ki"}}, {"range": {"zeit": {"gte": seit}}}]}}})
    n, markiert, ohne_start = 0, 0, 0
    for h in res["hits"]["hits"]:
        s = h["_source"]
        start = (s.get("ai_provenance") or {}).get("start")
        if not start:
            ohne_start += 1
            continue
        markiert += await markiere_gesehen(h["_id"], s.get("relevanz"), start,
                                           datetime.fromisoformat(str(s["zeit"]).replace("Z", "+00:00")), stunden)
        n += 1
    return {"befunde": len(res["hits"]["hits"]), "mit_start": n, "ohne_start": ohne_start, "nachrichten_markiert": markiert}


async def liste(status: str | None = None, suche: str = "", limit: int = 100) -> dict:
    """Bekannte Fehler, zuletzt aufgetretene zuerst; mit Gesamtzahlen je Status."""
    from app.core.opensearch import get_opensearch
    filt = [{"term": {"status": status}}] if status else []
    must = [{"simple_query_string": {"query": suche, "fields": ["fehler", "titel", "hosts", "befund"],
                                     "default_operator": "and"}}] if suche.strip() else []
    try:
        res = await get_opensearch().search(index=INDEX, body={
            "size": max(0, min(limit, 500)), "query": {"bool": {"filter": filt, "must": must}},
            "sort": [{"zuletzt": "desc"}], "track_total_hits": True,
            "aggs": {"status": {"terms": {"field": "status"}},
                     "wiederholungen": {"sum": {"field": "wiederholungen"}}}})
    except Exception as e:  # noqa: BLE001
        if "index_not_found" in str(e):
            return {"gesamt": 0, "je_status": {}, "wiederholungen": 0, "eintraege": [],
                    "hinweis": "noch nichts gelernt (Index fehlt)"}
        raise
    return {"gesamt": res["hits"]["total"]["value"],
            "je_status": {b["key"]: b["doc_count"] for b in res["aggregations"]["status"]["buckets"]},
            "wiederholungen": int(res["aggregations"]["wiederholungen"]["value"] or 0),
            "eintraege": [{**h["_source"], "id": h["_id"]} for h in res["hits"]["hits"]]}


async def vergiss(eintrag_id: str, grund: str, wer: str) -> dict | None:
    """Ein Mensch widerlegt: der Fehler gilt nicht mehr als bekannt — beim nächsten Auftreten
    analysiert die KI neu. Der Grund ist Pflicht (zureichender Grund)."""
    from app.core.opensearch import get_opensearch
    os_client = get_opensearch()
    try:
        await os_client.update(index=INDEX, id=eintrag_id, refresh=True, body={"doc": {
            "status": "widerlegt", "herkunft": "mensch", "grund": grund[:2000], "geaendert_von": wer}})
    except Exception as e:  # noqa: BLE001
        if "not_found" in str(e) or "document_missing" in str(e):
            return None
        raise
    r = await os_client.get(index=INDEX, id=eintrag_id)
    return {**r["_source"], "id": r["_id"]}


async def nachtragen(tage: float = 7) -> dict:
    """Einmalig: aus den Befunden der letzten Tage lernen (ältester zuerst, der neueste gewinnt).
    Gesehen ist hier nur, was der Befund zitiert — die Lage alter Analysen ist nicht gespeichert."""
    from datetime import timedelta

    from app.core.opensearch import get_opensearch
    from app.services.log_watch.historie import PRAEFIX
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    res = await get_opensearch().search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 5000, "sort": [{"zeit": "asc"}], "_source": ["zeit", "relevanz", "ai_provenance", "text"],
        "query": {"bool": {"filter": [{"term": {"quelle": "ki"}}, {"range": {"zeit": {"gte": seit}}}]}}})
    befunde, eintraege_n = 0, 0
    for h in res["hits"]["hits"]:
        s = h["_source"]
        pv = s.get("ai_provenance") or {}
        ids = [b.get("id") for b in pv.get("belege") or []]
        titel = pv.get("titel") or (s.get("text") or "").split("\n")[0]
        n = await lerne(h["_id"], titel, s.get("relevanz"), ids, [],
                        datetime.fromisoformat(str(s["zeit"]).replace("Z", "+00:00")))
        befunde += 1 if n else 0
        eintraege_n += n
    return {"befunde_gelesen": len(res["hits"]["hits"]), "befunde_mit_eintrag": befunde, "eintraege_geschrieben": eintraege_n}
