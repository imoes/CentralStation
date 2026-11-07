"""Eine Suche über die Historie `cs-logs-*` — für Cockpit, Netzplan, MCP und die KI.

Ein Filter (filter.py) + Zeitraum + optional Bedeutung (kNN) → Treffer und Facetten. Die
Facetten sind die Hashtags des Ergebnisses je Art (`host:`, `thema:` …): dieselbe Sicht für
Mensch und KI, kein zweiter Suchweg (Plan „Eine Suche über den ganzen Korpus“).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.services.log_watch import filter as flt
from app.services.log_watch.historie import PRAEFIX

#: Felder eines Treffers, die zurückgegeben werden (der Vektor nie, `roh` nur auf Wunsch)
QUELLFELDER = ["zeit", "quelle", "host", "relevanz", "urteil", "herkunft", "kriterien",
               "hashtags", "text", "gruende", "gruppe_anzahl", "ai_befund"]
#: so viele Hashtag-Werte werden für die Facetten aggregiert, bevor nach Art geteilt wird
FACETTEN_TIEFE = 300


def abfrage(filtertext: str, objekte: dict[str, str] | None, seit: datetime, bis: datetime) -> dict:
    """Filtertext + Zeitraum → OpenSearch-Query. Leerer Filter = alles im Zeitraum."""
    zeit = {"range": {"zeit": {"gte": seit.isoformat(), "lt": bis.isoformat()}}}
    if not (filtertext or "").strip():
        return {"bool": {"filter": [zeit]}}
    return {"bool": {"filter": [zeit], "must": [flt.opensearch(filtertext, objekte)]}}


def facetten(buckets: list[dict]) -> dict[str, list[dict]]:
    """terms-Buckets über `hashtags` → je Art die Werte mit Anzahl (absteigend)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for b in buckets:
        art, _, wert = b["key"].partition(":")
        out[art].append({"wert": wert, "hashtag": b["key"], "anzahl": b["doc_count"]})
    return dict(out)


def treffer(hit: dict, mit_roh: bool = False) -> dict:
    s = hit.get("_source") or {}
    t = {"id": hit["_id"], **{k: s[k] for k in QUELLFELDER if k in s}}
    if mit_roh and "roh" in s:
        t["roh"] = s["roh"]
    if hit.get("_score") is not None and "_bedeutung" in hit:
        t["aehnlichkeit"] = hit["_score"]
    return t


def zeitraum(stunden: float, jetzt: datetime | None = None) -> tuple[datetime, datetime]:
    bis = jetzt or datetime.now(timezone.utc)
    return bis - timedelta(hours=stunden), bis


async def suche(filtertext: str = "", *, stunden: float = 24, limit: int = 20,
                bedeutung: str = "", mit_roh: bool = False, objekte: dict[str, str] | None = None,
                sortierung: str = "relevanz") -> dict:
    """Treffer + Facetten zu einem Filter. `bedeutung`: Text → die ähnlichsten Nachrichten
    (kNN über `vektor`), auf die derselbe Filter wirkt.

    Rückgabe: {"filter", "zeitraum", "gesamt", "treffer": [...], "facetten": {art: [...]}}.
    Ein unlesbarer Filter liefert {"fehler": Grund mit Stelle} statt einer leeren Liste.
    """
    from app.core.opensearch import get_opensearch

    seit, bis = zeitraum(stunden)
    try:
        q = abfrage(filtertext, objekte, seit, bis)
    except flt.FilterFehler as e:
        return {"filter": filtertext, "fehler": str(e)}
    body: dict = {"size": max(0, min(limit, 200)), "track_total_hits": True,
                  "_source": QUELLFELDER + (["roh"] if mit_roh else []),
                  "aggs": {"tags": {"terms": {"field": "hashtags", "size": FACETTEN_TIEFE}}}}
    if bedeutung.strip():
        from app.services.log_watch.embed import einbetten
        (v,) = await einbetten([bedeutung])
        body["query"] = {"knn": {"vektor": {"vector": v, "k": max(limit, 50), "filter": q}}}
    else:
        body["query"] = q
        body["sort"] = ([{"relevanz": "desc"}, {"zeit": "desc"}] if sortierung == "relevanz"
                        else [{"zeit": "desc"}])
    res = await get_opensearch().search(index=f"{PRAEFIX}*", body=body, ignore_unavailable=True)
    hits = res["hits"]["hits"]
    if bedeutung.strip():
        for h in hits:
            h["_bedeutung"] = True
    return {
        "filter": filtertext, "bedeutung": bedeutung or None,
        "zeitraum": {"seit": seit.isoformat(), "bis": bis.isoformat(), "stunden": stunden},
        "gesamt": res["hits"]["total"]["value"],
        "treffer": [treffer(h, mit_roh) for h in hits],
        "facetten": facetten(((res.get("aggregations") or {}).get("tags") or {}).get("buckets", [])),
    }


# ── Netzplan: Knoten = Hashtags des Ergebnisses, Kante = gemeinsam in derselben Nachricht ──

#: Hashtag-Arten, die Knoten werden. Nicht: `muster:` (Hash, unlesbar — steht in der Facette),
#: `hostliste:` (Zustand der Zuordnung), `regelgruppe:` (doppelt zu `regel:`).
NETZ_ARTEN = ("host", "befund", "incident", "thema", "dienst", "regel", "mitre", "mitre-technik", "label", "netz", "netzbereich", "zustand",
              "kritikalitaet", "signal", "quelle", "app", "ort", "gegenstelle")
#: höchstens so viele Knoten je Art — sonst verdrängen 15 Hosts jedes Thema
JE_ART = 5
#: Hashtags an mehr als diesem Anteil der Nachrichten sagen über Zusammenhänge nichts (sie
#: hängen an allem) — gemessen 07.11.: netz:intern stand als Nabe in der Mitte des Netzplans
NABE_ANTEIL = 0.8


def knoten_waehlen(buckets: list[dict], anzahl: int, fokus: set[str] = frozenset(),
                   gesamt: int = 0, arten: tuple[str, ...] = NETZ_ARTEN,
                   je_art: dict[str, int] | None = None) -> list[dict]:
    """Knoten aus terms-Buckets (absteigend): je Art höchstens JE_ART, insgesamt `anzahl`.
    Hashtags im Filter (`fokus`) sind immer dabei — der Netzplan zeigt, wovon er ausgeht.
    Naben (an mehr als NABE_ANTEIL der `gesamt` Nachrichten) fallen weg, außer im Fokus."""
    je_art_n: dict[str, int] = defaultdict(int)
    out = []
    for b in buckets:
        art, _, wert = b["key"].partition(":")
        if art not in arten and b["key"] not in fokus:
            continue
        if gesamt and b["doc_count"] > NABE_ANTEIL * gesamt and b["key"] not in fokus:
            continue
        grenze = (je_art or {}).get(art, JE_ART)
        if b["key"] not in fokus and (je_art_n[art] >= grenze or len(out) >= anzahl):
            continue
        je_art_n[art] += 1
        out.append({"id": b["key"], "art": art, "wert": wert, "anzahl": b["doc_count"],
                    "fokus": b["key"] in fokus})
    return out


def kanten(adj_buckets: list[dict], ids: list[str]) -> list[dict]:
    """adjacency_matrix-Buckets („i&j“ mit Index-Namen n0, n1 …) → Kanten {a, b, anzahl}."""
    out = []
    for b in adj_buckets:
        teile = b["key"].split("&")
        if len(teile) != 2 or b["doc_count"] == 0:
            continue
        a, c = (ids[int(t[1:])] for t in teile)
        out.append({"a": a, "b": c, "anzahl": b["doc_count"]})
    return sorted(out, key=lambda k: -k["anzahl"])


def _fokus(filtertext: str) -> set[str]:
    try:
        k = flt.lies(filtertext) if (filtertext or "").strip() else None
    except flt.FilterFehler:
        return set()
    out, stapel = set(), [k] if k else []
    while stapel:
        x = stapel.pop()
        if x.art == "tag":
            out.add(x.wert)
        elif x.art in ("und", "oder"):
            stapel.extend(x.kinder)
    return out


async def netzplan(filtertext: str = "", *, stunden: float = 24, knoten: int = 15,
                   objekte: dict[str, str] | None = None, sicht: str = "alles") -> dict:
    """Knoten und Kanten zum Ergebnis eines Filters (zwei Aggregationen, keine Dokumente).

    Eine Kante heißt: beide Hashtags stehen an derselben Nachricht, so oft wie `anzahl` —
    nie bloß zeitliche Nähe (kein post hoc)."""
    from app.core.opensearch import get_opensearch

    seit, bis = zeitraum(stunden)
    try:
        q = abfrage(filtertext, objekte, seit, bis)
    except flt.FilterFehler as e:
        return {"filter": filtertext, "fehler": str(e)}
    os_client = get_opensearch()
    fehler_sicht = sicht == "fehler"
    terms: dict = {"field": "hashtags", "size": FACETTEN_TIEFE}
    if fehler_sicht:
        # Problemobjekte zuerst: dringend und unbearbeitet, dann dringend, dann Menge
        terms.update({"include": "(" + "|".join(FEHLER_ARTEN) + "):.*",
                      "order": [{"dringend_offen": "desc"}, {"dringend": "desc"}, {"_count": "desc"}]})
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, request_cache=True, body={
        "size": 0, "track_total_hits": True, "query": q,
        "aggs": {"tags": {"terms": terms, **({"aggs": {k_: ZUSTAND_AGGS[k_] for k_ in ("dringend", "dringend_offen")}}
                                            if fehler_sicht else {})}}})
    gesamt = res["hits"]["total"]["value"]
    buckets = res["aggregations"]["tags"]["buckets"]
    naben = [b["key"] for b in buckets if gesamt and b["doc_count"] > NABE_ANTEIL * gesamt
             and b["key"].partition(":")[0] in NETZ_ARTEN]
    k = knoten_waehlen(buckets, max(2, min(knoten, 40)), _fokus(filtertext), gesamt,
                       arten=FEHLER_ARTEN if fehler_sicht else NETZ_ARTEN,
                       je_art=FEHLER_JE_ART if fehler_sicht else None)
    ids = [x["id"] for x in k]
    kn: list[dict] = []
    if ids:
        filt = {f"n{i}": {"term": {"hashtags": t}} for i, t in enumerate(ids)}
        aggs: dict = {"z": {"filters": {"filters": filt}, "aggs": ZUSTAND_AGGS}}
        if len(ids) >= 2:
            aggs["m"] = {"adjacency_matrix": {"filters": filt}}
        adj = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, request_cache=True, body={
            "size": 0, "query": q, "aggs": aggs})
        if "m" in adj["aggregations"]:
            kn = kanten(adj["aggregations"]["m"]["buckets"], ids)
        zustand(k, adj["aggregations"]["z"]["buckets"])
        await _befund_titel(os_client, k)
    return {"filter": filtertext, "gesamt": gesamt, "knoten": k, "kanten": kn,
            "ausgeblendet_naben": naben,
            "zeitraum": {"seit": seit.isoformat(), "bis": bis.isoformat(), "stunden": stunden},
            "satz": satz(k, gesamt, stunden),
            "regel": "Kante = beide Hashtags an derselben Nachricht; Zahl = Nachrichten. Stufe je Knoten: "
                     "dringend = mindestens eine Nachricht mit Relevanz 3; offen = Nachrichten ohne Befund"}


# ── Fehler-Netzplan: Problemzustand je Knoten (Nutzer 07.11.: „auf einen Blick sehen, wo die Probleme
# sind; jeder Knoten ist ein Objekt, Host oder Tag“) — aus derselben Suche wie Knoten und Kanten ──

#: Objekte des Fehler-Netzplans: Hosts und die Tags, die ein Problem benennen. Nicht: Kontext-Tags
#: (quelle, netz, kritikalitaet, ort, signal, zustand) — gemessen 07.11.: nach Menge gewählt füllten sie
#: alle 15 Knoten (netz:intern 6 075, kritikalitaet:prod 5 872 …) und kein Host war zu sehen. Sie bleiben
#: Filter und Facetten.
FEHLER_ARTEN = ("host", "befund", "incident", "thema", "dienst", "regel", "mitre-technik", "app", "gegenstelle", "label")
FEHLER_JE_ART = {"host": 8, "befund": 4, "incident": 4, "thema": 3, "dienst": 3, "regel": 3, "mitre-technik": 2, "app": 2,
                 "gegenstelle": 2, "label": 2}
_OFFEN = [{"prefix": {"hashtags": "befund:"}}, {"term": {"quelle": "ki"}}]

#: je Knoten: dringende Nachrichten (Relevanz 3), offene (an keinem Befund), dringend und offen,
#: verknüpfte Befunde, letzte Zeit
ZUSTAND_AGGS = {
    "dringend": {"filter": {"range": {"relevanz": {"gte": 3}}}},
    "dringend_offen": {"filter": {"bool": {"filter": [{"range": {"relevanz": {"gte": 3}}}], "must_not": _OFFEN}}},
    "offen": {"filter": {"bool": {"must_not": _OFFEN}}},
    "befunde": {"terms": {"field": "hashtags", "include": "befund:.*", "size": 5}},
    "zuletzt": {"max": {"field": "zeit"}},
}
#: Stufen eines Knotens — erschöpfend, die erste zutreffende gilt:
#:   dringend_offen  Relevanz 3, an keinem Befund (niemand hat es untersucht) — rot
#:   dringend        Relevanz 3, alles davon an Befunden — orange
#:   auffaellig      relevant (Relevanz 2), nichts Dringendes — gelb
STUFEN = ("dringend_offen", "dringend", "auffaellig")


def zustand(knoten: list[dict], buckets: dict) -> None:
    """Zustand je Knoten aus den filters-Buckets (n0, n1 …) setzen. Rein — testbar."""
    for i, x in enumerate(knoten):
        b = buckets.get(f"n{i}") or {}
        x["dringend"] = (b.get("dringend") or {}).get("doc_count", 0)
        x["dringend_offen"] = (b.get("dringend_offen") or {}).get("doc_count", 0)
        x["offen"] = (b.get("offen") or {}).get("doc_count", 0)
        x["befunde"] = [t["key"] for t in (b.get("befunde") or {}).get("buckets", []) if t["key"] != x["id"]]
        x["zuletzt"] = (b.get("zuletzt") or {}).get("value_as_string")
        # alles in der Historie ist relevant (Relevanz ≥ 2) — dringend ist, was Relevanz 3 trägt
        x["stufe"] = ("dringend_offen" if x["dringend_offen"] else "dringend" if x["dringend"] else "auffaellig")


async def _befund_titel(os_client, knoten: list[dict]) -> None:
    """Befund- und Incident-Knoten tragen ihren Titel — ein Objekt, kein Hash."""
    inc = [x["wert"] for x in knoten if x["art"] == "incident"]
    if inc:
        from app.services.log_watch.incident_objekt import titel as incident_titel
        t = await incident_titel(inc)
        for x in knoten:
            if x["art"] == "incident":
                i = t.get(x["wert"]) or {}
                x["titel"] = i.get("titel") or ""
                x["incident"] = {"kind": i.get("kind"), "status": i.get("status")}
    ids = [f"ki:{x['wert']}" for x in knoten if x["art"] == "befund"]
    if not ids:
        return
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": len(ids), "query": {"ids": {"values": ids}}, "_source": ["ai_provenance.titel", "relevanz"]})
    titel = {h["_id"]: (h["_source"].get("ai_provenance") or {}).get("titel") for h in res["hits"]["hits"]}
    for x in knoten:
        if x["art"] == "befund":
            x["titel"] = titel.get(f"ki:{x['wert']}") or ""


def satz(knoten: list[dict], gesamt: int, stunden: float) -> str:
    """Ein Satz über dem Netzplan, der in Worten sagt, was man sieht (wie im Recherche-Tool)."""
    if not gesamt:
        return f"Keine relevanten Nachrichten in {stunden:g} h."
    hosts = sorted((x for x in knoten if x["art"] == "host" and not x.get("fokus")),
                   key=lambda x: (-x.get("dringend_offen", 0), -x.get("dringend", 0), -x["anzahl"]))
    unbearbeitet = [x for x in hosts if x.get("dringend_offen")]
    teile = [f"{gesamt} relevante Nachrichten in {stunden:g} h"]
    if unbearbeitet:
        teile.append("dringend und ohne Befund: " + ", ".join(
            f"{x['wert']} ({x['dringend_offen']})" for x in unbearbeitet[:3]))
    elif any(x.get("dringend") for x in hosts):
        teile.append("dringend, aber untersucht: " + ", ".join(
            f"{x['wert']} ({x['dringend']})" for x in hosts if x.get("dringend"))[:200])
    elif hosts:
        teile.append("am häufigsten: " + ", ".join(f"{x['wert']} ({x['anzahl']})" for x in hosts[:3]))
    return " · ".join(teile) + "."
