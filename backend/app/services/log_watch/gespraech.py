"""Gesprächsprotokoll des agentischen Modus: jeder Zug einer KI-Analyse ist ein Dokument.

Laden heißt suchen: das Protokoll eines Hosts ist `host:<name>` über `cs-gespraech-*`, sortiert
nach Zeit — dieselbe Filtersprache wie die Historie. Ein Zug trägt die Hashtags seines Falls
(Host-Felder aus CheckMK, Muster, Befund); so findet `netz:dmz` auch Gespräche über DMZ-Hosts.

Eigener Index, nicht `cs-logs-*`: Gesprächszüge sind keine Nachrichten über das Netz — in der
Historie würden sie Netzplan, Trends und Prüfung verfälschen.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

PRAEFIX = "cs-gespraech-"
VORLAGE = "cs-gespraech"
#: Rollen eines Zugs — erschöpfend
ROLLEN = ("worker", "ki", "werkzeug", "befund", "mensch")
#: so viel Text eines Zugs wird gespeichert (Werkzeugantworten können groß sein)
TEXT_MAX = 20000

MAPPING = {
    "dynamic": "strict",
    "properties": {
        "zeit": {"type": "date"},
        "sitzung": {"type": "keyword"},          # eine Analyse = eine Sitzung
        "zug": {"type": "short"},                # Reihenfolge in der Sitzung
        "rolle": {"type": "keyword"},
        "fall": {"type": "keyword"},             # Startfilter des Falls (muster:… / host:…)
        "hosts": {"type": "keyword"},
        "hashtags": {"type": "keyword"},
        "werkzeug": {"type": "keyword"},
        "argumente": {"type": "object", "enabled": False},
        "text": {"type": "text"},
        "befund": {"type": "keyword"},
        "modell": {"type": "keyword"},
        "ki_s": {"type": "float"},
    },
}
VORLAGE_BODY = {"index_patterns": [f"{PRAEFIX}*"], "priority": 100,
                "template": {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
                             "mappings": MAPPING}}


def index_name(zeit: datetime) -> str:
    return f"{PRAEFIX}{zeit.astimezone(timezone.utc):%Y.%m}"


def zuege(sitzung: str, fall: dict, hashtags: list[str], verlauf: list[dict], jetzt: datetime) -> list[dict]:
    """Protokoll-Einträge einer Sitzung aus dem Verlauf {rolle, text, werkzeug?, argumente?, …}.
    Rein — testbar. Rollen außerhalb ROLLEN sind ein Fehler, kein stiller Zug."""
    out = []
    for i, v in enumerate(verlauf):
        if v["rolle"] not in ROLLEN:
            raise ValueError(f"Rolle {v['rolle']!r} unbekannt (erlaubt: {', '.join(ROLLEN)})")
        out.append({
            "zeit": (v.get("zeit") or jetzt).astimezone(timezone.utc).isoformat(),
            "sitzung": sitzung, "zug": i, "rolle": v["rolle"], "fall": fall["start"],
            "hosts": list(fall["hosts"]), "hashtags": sorted(set(hashtags) | {f"host:{h}" for h in fall["hosts"]}),
            **{k: v[k] for k in ("werkzeug", "argumente", "befund", "modell", "ki_s") if v.get(k) is not None},
            "text": str(v.get("text") or "")[:TEXT_MAX],
        })
    return out


async def stelle_bereit() -> None:
    from app.core.opensearch import get_opensearch
    await get_opensearch().indices.put_index_template(name=VORLAGE, body=VORLAGE_BODY)


async def schreibe(eintraege: list[dict]) -> int:
    from app.core.opensearch import get_opensearch
    if not eintraege:
        return 0
    body: list[dict] = []
    for e in eintraege:
        body += [{"index": {"_index": index_name(datetime.fromisoformat(e["zeit"])),
                            "_id": f"{e['sitzung']}:{e['zug']}"}}, e]
    res = await get_opensearch().bulk(body=body, refresh=True)
    fehler = [i for i in res.get("items", []) if (i.get("index") or {}).get("error")]
    if fehler:
        log.warning("Gespräch: %d von %d Zügen abgelehnt: %s", len(fehler), len(eintraege),
                    str(fehler[0]["index"]["error"])[:300])
    return len(eintraege) - len(fehler)


def neue_sitzung() -> str:
    return uuid.uuid4().hex[:12]


async def lade(filtertext: str, *, tage: float = 7, sitzungen: int = 3) -> list[dict]:
    """Die letzten `sitzungen` Sitzungen zum Filter (z. B. host:x), je Sitzung die Züge in
    Reihenfolge. Laden ist eine Suche (Filtersprache über die Hashtags der Züge)."""
    from app.core.opensearch import get_opensearch
    from app.services.log_watch import filter as flt
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    os_client = get_opensearch()
    q = {"bool": {"filter": [{"range": {"zeit": {"gte": seit}}}, flt.opensearch(filtertext)]}}
    agg = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 0, "query": q,
        "aggs": {"s": {"terms": {"field": "sitzung", "size": sitzungen, "order": {"z": "desc"}},
                       "aggs": {"z": {"max": {"field": "zeit"}}}}}})
    ids = [b["key"] for b in ((agg.get("aggregations") or {}).get("s") or {}).get("buckets", [])]
    if not ids:
        return []
    res = await os_client.search(index=f"{PRAEFIX}*", ignore_unavailable=True, body={
        "size": 500, "query": {"terms": {"sitzung": ids}}, "sort": [{"zeit": "asc"}, {"zug": "asc"}]})
    je: dict[str, list[dict]] = {}
    for h in res["hits"]["hits"]:
        je.setdefault(h["_source"]["sitzung"], []).append(h["_source"])
    return [{"sitzung": s, "zuege": je.get(s, [])} for s in reversed(ids)]
