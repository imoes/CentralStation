"""Wazuh: Felder aus dem Mapping von `wazuh-alerts-*`, gezählt per OpenSearch-Aggregation
direkt am Indexer (Abdeckung = `exists`-Filter, Kardinalität = `cardinality`).

Wazuh legt Tagesindizes an und mappt Felder dynamisch — die Feldliste ist die Vereinigung
der letzten Tage, damit ein Feld, das heute (noch) nicht vorkam, nicht „verschwindet“.
"""
from __future__ import annotations

import logging

from app.services.log_watch.catalog import BEISPIELE_JE_FELD, Feld, braucht_werte

log = logging.getLogger(__name__)

INDEX = "wazuh-alerts-*"
#: Tagesindizes, deren Mapping zusammen die Feldliste ergibt.
TAGE_MAPPING = 7
FELDER_JE_ANFRAGE = 50
_NUMERISCH = {"long", "integer", "short", "byte", "double", "float", "half_float",
              "scaled_float", "unsigned_long"}
#: Diese Typen lassen sich direkt aggregieren (text nur über ein keyword-Unterfeld).
_AGGREGIERBAR = _NUMERISCH | {"keyword", "ip", "date", "boolean"}


def felder_aus_mapping(mappings: dict) -> tuple[list[Feld], dict[str, str | None]]:
    """`GET <index>/_mapping` (mehrere Indizes) → Felder und je Feld das Aggregationsfeld.

    Rückgabe: (Felder sortiert, {feld: agg_feld | None}). None = nicht aggregierbar (z. B.
    reiner Text, geo_point): Abdeckung wird gezählt, Kardinalität nicht.
    """
    typen: dict[str, str] = {}
    agg: dict[str, str | None] = {}

    def lauf(props: dict, prefix: str) -> None:
        for k, v in (props or {}).items():
            name = f"{prefix}{k}"
            if "properties" in v:
                lauf(v["properties"], name + ".")
                continue
            t = v.get("type", "object")
            typen.setdefault(name, t)
            if t in _AGGREGIERBAR:
                agg[name] = name
            elif (v.get("fields") or {}).get("keyword", {}).get("type") == "keyword":
                agg[name] = f"{name}.keyword"
            else:
                agg.setdefault(name, None)

    for idx in mappings.values():
        lauf((idx.get("mappings") or {}).get("properties") or {}, "")
    felder = [Feld(name=n, typ=t[:64], numerisch=t in _NUMERISCH, aggregierbar=agg[n] is not None)
              for n, t in sorted(typen.items())]
    return felder, agg


def stats_aus_aggs(aggs: dict, namen: list[str]) -> dict[str, dict]:
    """Antwort mit n{i} (filter exists) und c{i} (cardinality) → {feld: {mit_feld, kardinalitaet}}."""
    out = {}
    for i, name in enumerate(namen):
        n = (aggs.get(f"n{i}") or {}).get("doc_count")
        if n is None:
            continue
        c = aggs.get(f"c{i}")
        out[name] = {"mit_feld": int(n), "kardinalitaet": int(c["value"]) if c else None}
    return out


def werte_aus_terms(agg: dict) -> list[dict]:
    return [{"wert": str(b.get("key_as_string", b.get("key")))[:200], "anzahl": int(b["doc_count"])}
            for b in (agg or {}).get("buckets") or []]


class WazuhQuelle:
    name = "wazuh"
    fenster = "24 h, alle Alerts"

    def __init__(self, w):
        self.w = w
        self._agg: dict[str, str | None] = {}

    def _auth(self) -> tuple[str, str]:
        c = self.w.credentials
        return c.get("indexer_username") or "admin", c.get("indexer_password") or ""

    async def felder(self) -> list[Feld]:
        base = self.w._indexer_base()
        async with self.w._client(timeout=60.0) as client:
            r = await client.get(f"{base}/_cat/indices/{INDEX}",
                                 params={"h": "index", "format": "json"}, auth=self._auth())
            r.raise_for_status()
            indizes = sorted(x["index"] for x in r.json())[-TAGE_MAPPING:]
            if not indizes:
                return []
            r = await client.get(f"{base}/{','.join(indizes)}/_mapping", auth=self._auth())
            r.raise_for_status()
        felder, self._agg = felder_aus_mapping(r.json())
        return felder

    async def _suche(self, aggs: dict) -> dict:
        body = {"size": 0, "track_total_hits": True,
                "query": {"range": {"timestamp": {"gte": "now-24h"}}}, "aggs": aggs}
        async with self.w._client(timeout=180.0) as client:
            return await self.w._query_indexer(client, INDEX, body)

    async def messe(self, felder: list[Feld]) -> tuple[int, dict[str, dict]]:
        gesamt = 0
        stats: dict[str, dict] = {}
        for i in range(0, len(felder), FELDER_JE_ANFRAGE):
            teil = felder[i:i + FELDER_JE_ANFRAGE]
            aggs: dict = {}
            for j, f in enumerate(teil):
                aggs[f"n{j}"] = {"filter": {"exists": {"field": f.name}}}
                if self._agg.get(f.name):
                    aggs[f"c{j}"] = {"cardinality": {"field": self._agg[f.name]}}
            try:
                res = await self._suche(aggs)
            except Exception as e:  # noqa: BLE001 — ein abgelehnter Block stoppt den Rest nicht
                log.warning("Feldkatalog wazuh: %d Felder nicht gemessen: %s", len(teil), e)
                continue
            gesamt = max(gesamt, int((res.get("hits") or {}).get("total", {}).get("value") or 0))
            stats.update(stats_aus_aggs(res.get("aggregations") or {}, [f.name for f in teil]))

        kandidaten = [f for f in felder
                      if self._agg.get(f.name) and braucht_werte(f, stats.get(f.name))]
        for i in range(0, len(kandidaten), 25):
            teil = kandidaten[i:i + 25]
            aggs = {f"t{j}": {"terms": {"field": self._agg[f.name], "size": BEISPIELE_JE_FELD}}
                    for j, f in enumerate(teil)}
            try:
                res = await self._suche(aggs)
            except Exception as e:  # noqa: BLE001
                log.warning("Feldkatalog wazuh: Beispielwerte fehlgeschlagen: %s", e)
                continue
            for j, f in enumerate(teil):
                stats[f.name]["beispielwerte"] = werte_aus_terms(
                    (res.get("aggregations") or {}).get(f"t{j}"))
        return gesamt, stats
