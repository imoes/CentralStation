"""Graylog: Felder aus `/api/views/fields`, gezählt per Views-Pivot (`count`/`card`).

Graylog zählt serverseitig über alle Nachrichten des Fensters — es werden keine
Nachrichten übertragen.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.services.log_watch.catalog import BEISPIELE_JE_FELD, Feld, braucht_werte, rolle_nach_name

log = logging.getLogger(__name__)

#: Kurz, weil Graylog je Feld über alle Nachrichten des Fensters zählt (≈ 9 Mio/h).
MESSFENSTER = timedelta(minutes=15)
#: Höchstens so viele Pivot-Serien je Anfrage.
SERIEN_JE_ANFRAGE = 100
#: So bezeichnet Graylog „Feld fehlt“ in einer Werteliste — kein echter Wert.
LEER = "(Empty Value)"
#: Nachrichten je Feld, wenn Graylog die Werteliste verweigert.
STICHPROBE = 200


def felder_aus_graylog(antwort: list[dict]) -> list[Feld]:
    """`GET /api/views/fields` → Felder (Typ z. B. "string", "compound(int,long)")."""
    out = []
    for f in antwort:
        name = f.get("name")
        if not name:
            continue
        t = f.get("type") or {}
        props = t.get("properties") or []
        out.append(Feld(name=name, typ=str(t.get("type") or "?")[:64],
                        numerisch="numeric" in props, aggregierbar="enumerable" in props))
    return out


def serien_aus_pivot(search_type: dict) -> dict[str, int]:
    """Pivot ohne Zeilengruppen → {"count(x)": n, "card(x)": n, "count()": n}."""
    out: dict[str, int] = {}
    for row in search_type.get("rows") or []:
        if row.get("key"):
            continue
        for v in row.get("values") or []:
            key = (v.get("key") or [None])[-1]
            if key is not None and isinstance(v.get("value"), (int, float)):
                out[key] = int(v["value"])
    return out


def werte_aus_pivot(search_type: dict) -> list[dict]:
    """Pivot mit einer Zeilengruppe → [{"wert", "anzahl"}], häufigste zuerst."""
    out = []
    for row in search_type.get("rows") or []:
        if row.get("source") != "leaf" or not row.get("key"):
            continue
        n = next((v.get("value") for v in row.get("values") or []
                  if isinstance(v.get("value"), (int, float))), None)
        if n is not None and row["key"][0] != LEER:
            out.append({"wert": str(row["key"][0])[:200], "anzahl": int(n)})
    return sorted(out, key=lambda x: -x["anzahl"])


_LUCENE_SONDER = re.compile(r'([+\-=&|><!(){}\[\]^"~*?:\\/ ])')


def exists_query(feld: str) -> str:
    """Lucene „Feld vorhanden“ mit maskierten Sonderzeichen im Feldnamen."""
    return "_exists_:" + _LUCENE_SONDER.sub(r"\\\1", feld)


def werte_aus_stichprobe(werte: list) -> list[dict]:
    """Häufigste Werte einer Nachrichten-Stichprobe, je Eintrag als `stichprobe` markiert."""
    zaehl = Counter(str(v)[:200] for v in werte if v not in (None, "", LEER))
    return [{"wert": w, "anzahl": n, "stichprobe": True}
            for w, n in zaehl.most_common(BEISPIELE_JE_FELD)]


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _pivot(id_: str, series: list[dict], row_field: str | None = None) -> dict:
    return {"id": id_, "type": "pivot",
            "row_groups": ([{"type": "values", "fields": [row_field],
                             "limit": BEISPIELE_JE_FELD}] if row_field else []),
            "column_groups": [], "series": series, "rollup": True, "sort": [], "streams": []}


class GraylogQuelle:
    name = "graylog"
    fenster = "15 min, alle Nachrichten"

    def __init__(self, gl):
        self.gl = gl

    async def _sync(self, queries: list[dict]) -> dict:
        async with self.gl._client(timeout=180.0) as client:
            r = await client.post(f"{self.gl.base_url}/api/views/search/sync",
                                  headers=self.gl._headers(), json={"queries": queries})
            r.raise_for_status()
        return r.json().get("results", {})

    async def felder(self) -> list[Feld]:
        async with self.gl._client(timeout=60.0) as client:
            r = await client.get(f"{self.gl.base_url}/api/views/fields",
                                 headers=self.gl._headers())
            r.raise_for_status()
        return felder_aus_graylog(r.json())

    async def messe(self, felder: list[Feld]) -> tuple[int, dict[str, dict]]:
        bis = datetime.now(timezone.utc).replace(microsecond=0)
        zeit = {"type": "absolute", "from": _iso(bis - MESSFENSTER), "to": _iso(bis)}
        query = {"type": "elasticsearch", "query_string": "*"}
        gesamt = 0
        stats: dict[str, dict] = {}

        # 1) Abdeckung und Kardinalität: zwei Serien je aggregierbarem Feld (Freitext ohne card)
        agg = [f for f in felder if f.aggregierbar]
        bloecke: list[list[tuple[Feld, list[dict]]]] = [[]]
        for f in agg:
            s = [{"type": "count", "id": f"count({f.name})", "field": f.name}]
            if (rolle_nach_name(f.name, f.numerisch) or ("",))[0] != "freitext":
                s.append({"type": "card", "id": f"card({f.name})", "field": f.name})
            if sum(len(x) for _, x in bloecke[-1]) + len(s) > SERIEN_JE_ANFRAGE:
                bloecke.append([])
            bloecke[-1].append((f, s))
        offen = [b for b in bloecke if b]
        while offen:
            b = offen.pop()
            series = [{"type": "count", "id": "count()"}] + [x for _, s in b for x in s]
            fehler = None
            try:
                res = (await self._sync([{"id": "q", "timerange": zeit, "query": query,
                                          "search_types": [_pivot("p", series)]}])).get("q", {})
                if res.get("errors"):
                    fehler = str(res["errors"][0].get("description", ""))[:200]
            except Exception as e:  # noqa: BLE001
                fehler = f"{type(e).__name__}: {e}"[:200]
            if fehler:
                # Ein einzelnes Feld kann den ganzen Block kexample: halbieren und neu
                # versuchen, bis das störende Feld allein übrig bleibt (→ nicht gemessen).
                if len(b) > 1:
                    offen += [b[:len(b) // 2], b[len(b) // 2:]]
                else:
                    log.warning("Feldkatalog graylog: %s nicht gemessen: %s", b[0][0].name, fehler)
                continue
            werte = serien_aus_pivot(res.get("search_types", {}).get("p", {}))
            gesamt = max(gesamt, werte.get("count()", 0))
            for f, _ in b:
                if f"count({f.name})" in werte:
                    stats[f.name] = {"mit_feld": werte[f"count({f.name})"],
                                     "kardinalitaet": werte.get(f"card({f.name})")}

        # 1b) Textfelder: nur Abdeckung, über „Feld vorhanden“ (eine Abfrage je Feld)
        text = [f for f in felder if not f.aggregierbar]
        for i in range(0, len(text), 25):
            teil = text[i:i + 25]
            qs = [{"id": f"x{j}", "timerange": zeit,
                   "query": {"type": "elasticsearch", "query_string": exists_query(f.name)},
                   "search_types": [_pivot("p", [{"type": "count", "id": "count()"}])]}
                  for j, f in enumerate(teil)]
            try:
                res = await self._sync(qs)
            except Exception as e:  # noqa: BLE001
                log.warning("Feldkatalog graylog: Textfelder nicht gemessen: %s", e)
                continue
            for j, f in enumerate(teil):
                r = res.get(f"x{j}", {})
                if r.get("errors"):
                    continue
                n = serien_aus_pivot(r.get("search_types", {}).get("p", {})).get("count()")
                if n is not None:
                    stats[f.name] = {"mit_feld": n, "kardinalitaet": None}

        # 2) Beispielwerte für Hashtag-/Kategorie-Kandidaten; ein abgelehnter Block wird
        #    halbiert wie oben — sonst fehlen 24 Felder wegen eines einzigen.
        kandidaten = [f for f in agg if braucht_werte(f, stats.get(f.name))]
        offen = [kandidaten[i:i + 25] for i in range(0, len(kandidaten), 25)]
        ohne_pivot: list[Feld] = []
        while offen:
            teil = offen.pop()
            sts = [_pivot(f"v{j}", [{"type": "count", "id": "count()"}], f.name)
                   for j, f in enumerate(teil)]
            fehler = None
            try:
                res = (await self._sync([{"id": "q", "timerange": zeit, "query": query,
                                          "search_types": sts}])).get("q", {})
                if res.get("errors") or not res.get("search_types"):
                    fehler = str((res.get("errors") or [{}])[0].get("description", "leer"))[:200]
            except Exception as e:  # noqa: BLE001
                fehler = f"{type(e).__name__}: {e}"[:200]
            if fehler:
                if len(teil) > 1:
                    offen += [teil[:len(teil) // 2], teil[len(teil) // 2:]]
                else:
                    ohne_pivot.append(teil[0])
                continue
            for j, f in enumerate(teil):
                st = res.get("search_types", {}).get(f"v{j}")
                if st:
                    stats[f.name]["beispielwerte"] = werte_aus_pivot(st)

        # 3) Felder, deren Werteliste Graylog verweigert (gemessen 06.11.: ~300, meist Typ
        #    je Tagesindex verschieden): Beispielwerte aus einer Nachrichten-Stichprobe,
        #    als solche markiert — keine vollständige Zählung.
        for i in range(0, len(ohne_pivot), 25):
            teil = ohne_pivot[i:i + 25]
            qs = [{"id": f"s{j}", "timerange": zeit,
                   "query": {"type": "elasticsearch", "query_string": exists_query(f.name)},
                   "search_types": [{"id": "m", "type": "messages", "limit": STICHPROBE,
                                     "offset": 0, "fields": [f.name], "streams": [],
                                     "sort": [{"field": "timestamp", "order": "DESC"}]}]}
                  for j, f in enumerate(teil)]
            try:
                res = await self._sync(qs)
            except Exception as e:  # noqa: BLE001
                log.warning("Feldkatalog graylog: Stichprobe für %d Felder fehlgeschlagen: %s",
                            len(teil), e)
                continue
            for j, f in enumerate(teil):
                msgs = (res.get(f"s{j}", {}).get("search_types", {}).get("m", {})
                        .get("messages") or [])
                werte = werte_aus_stichprobe([m.get("message", {}).get(f.name) for m in msgs])
                if werte:
                    stats[f.name]["beispielwerte"] = werte
        return gesamt, stats
