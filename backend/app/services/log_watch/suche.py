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
NETZ_ARTEN = ("host", "befund", "thema", "dienst", "regel", "mitre", "mitre-technik", "label", "netz", "netzbereich", "zustand",
              "kritikalitaet", "signal", "quelle", "app", "ort", "gegenstelle")
#: höchstens so viele Knoten je Art — sonst verdrängen 15 Hosts jedes Thema
JE_ART = 5


def knoten_waehlen(buckets: list[dict], anzahl: int, fokus: set[str] = frozenset()) -> list[dict]:
    """Knoten aus terms-Buckets (absteigend): je Art höchstens JE_ART, insgesamt `anzahl`.
    Hashtags im Filter (`fokus`) sind immer dabei — der Netzplan zeigt, wovon er ausgeht."""
    je_art: dict[str, int] = defaultdict(int)
    out = []
    for b in buckets:
        art, _, wert = b["key"].partition(":")
        if art not in NETZ_ARTEN:
            continue
        if b["key"] not in fokus and (je_art[art] >= JE_ART or len(out) >= anzahl):
            continue
        je_art[art] += 1
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
                   objekte: dict[str, str] | None = None) -> dict:
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
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, request_cache=True, body={
        "size": 0, "track_total_hits": True, "query": q,
        "aggs": {"tags": {"terms": {"field": "hashtags", "size": FACETTEN_TIEFE}}}})
    gesamt = res["hits"]["total"]["value"]
    k = knoten_waehlen(res["aggregations"]["tags"]["buckets"], max(2, min(knoten, 40)), _fokus(filtertext))
    ids = [x["id"] for x in k]
    kn: list[dict] = []
    if len(ids) >= 2:
        adj = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, request_cache=True, body={
            "size": 0, "query": q,
            "aggs": {"m": {"adjacency_matrix": {"filters": {
                f"n{i}": {"term": {"hashtags": t}} for i, t in enumerate(ids)}}}}})
        kn = kanten(adj["aggregations"]["m"]["buckets"], ids)
    return {"filter": filtertext, "gesamt": gesamt, "knoten": k, "kanten": kn,
            "zeitraum": {"seit": seit.isoformat(), "bis": bis.isoformat(), "stunden": stunden},
            "regel": "Kante = beide Hashtags an derselben Nachricht; Zahl = Nachrichten"}
