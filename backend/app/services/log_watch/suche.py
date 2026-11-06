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
               "hashtags", "text", "gruende", "gruppe_anzahl", "befund_ids", "ai_befund"]
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
