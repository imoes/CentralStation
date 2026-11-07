"""Feed-Suche wie in Graylog: unlesbare Suche ist ein benannter Fehler (nie „0 Treffer“), die
Gesamtzahl kommt mit, Top-Werte zählen nur zählbare Felder und nennen „andere“ und „fehlend“."""
import asyncio

import pytest
from opensearchpy.exceptions import RequestError

from app.services import feed_index as fi


class _OS:
    def __init__(self, antwort=None, fehler=None, mapping=None):
        self.antwort, self.fehler, self.mapping, self.koerper = antwort, fehler, mapping or {}, []
        self.indices = self

    async def search(self, index, body, ignore_unavailable=True):
        self.koerper.append(body)
        if self.fehler:
            raise self.fehler
        return self.antwort

    async def get_field_mapping(self, index, fields):
        return self.mapping


def _setze(monkeypatch, os):
    monkeypatch.setattr(fi, "get_opensearch", lambda: os)


def test_unlesbare_suche_ist_fehler_mit_grund(monkeypatch):
    _setze(monkeypatch, _OS(fehler=RequestError(400, "search_phase_execution_exception", {"error": {
        "root_cause": [{"type": "query_shard_exception", "reason": "Failed to parse query [source:(]"}]}})))
    with pytest.raises(fi.FeedQueryFehler, match=r"Failed to parse query \[source:\(\]"):
        asyncio.run(fi.suche_seite("cs-feed-*", "source:("))


def test_suche_liefert_treffer_und_gesamtzahl(monkeypatch):
    os = _OS(antwort={"hits": {"total": {"value": 812}, "hits": [{"_source": {"id": "a"}}]}})
    _setze(monkeypatch, os)
    items, gesamt = asyncio.run(fi.suche_seite("cs-feed-*", 'NOT source:graylog AND tags:oom', size=1,
                                               since_seconds=3600))
    assert items == [{"id": "a"}] and gesamt == 812
    q = os.koerper[0]
    assert os.koerper[0]["track_total_hits"] is True
    assert q["query"]["bool"]["must"][0]["query_string"]["query"] == "NOT source:graylog AND tags:oom"


def _mapping(**typen):
    return {"cs-feed-graylog": {"mappings": {n: {"mapping": {n.split(".")[-1]: {"type": t}}}
                                             for n, t in typen.items()}}}


def test_top_werte_zaehlt_ueber_keyword_mit_andere_und_fehlend(monkeypatch):
    os = _OS(antwort={"hits": {"total": {"value": 50}}, "aggregations": {
        "werte": {"buckets": [{"key": "cs-backend-1", "doc_count": 30}], "sum_other_doc_count": 5},
        "fehlend": {"doc_count": 15}}},
        mapping=_mapping(**{"metadata.container_name": "text", "metadata.container_name.keyword": "keyword"}))
    _setze(monkeypatch, os)
    r = asyncio.run(fi.top_werte("cs-feed-*", "source:graylog", "metadata.container_name"))
    assert r == {"feld": "metadata.container_name", "gezaehlt_ueber": "metadata.container_name.keyword",
                 "gesamt": 50, "werte": [{"wert": "cs-backend-1", "anzahl": 30}], "andere": 5, "fehlend": 15}
    assert os.koerper[0]["aggs"]["werte"]["terms"]["field"] == "metadata.container_name.keyword"


def test_top_werte_nennt_warum_nicht_zaehlbar(monkeypatch):
    _setze(monkeypatch, _OS(mapping=_mapping(body="text")))
    with pytest.raises(fi.FeedQueryFehler, match="Freitext"):
        asyncio.run(fi.top_werte("cs-feed-*", "", "body"))
    _setze(monkeypatch, _OS(mapping={}))
    with pytest.raises(fi.FeedQueryFehler, match="gibt es in cs-feed-\\* nicht"):
        asyncio.run(fi.top_werte("cs-feed-*", "", "metadata.gibtsnicht"))
    with pytest.raises(fi.FeedQueryFehler, match="ungültig"):
        asyncio.run(fi.top_werte("cs-feed-*", "", "doc['x']"))


def test_escape_wie_graylog_und_wie_frontend():
    # dieselben Fälle wie frontend feed-query.ts (Node-Prüfung)
    assert fi.escape_wert("cs-backend-1") == "cs\\-backend\\-1"
    assert fi.escape_wert("Filesystem /var") == '"Filesystem /var"'
    assert fi.escape_wert('a "b" c') == '"a \\"b\\" c"'
    assert fi.escape_wert("http://x:1/y?z") == "http\\:\\/\\/x\\:1\\/y\\?z"


def test_zerlege_eingabe():
    assert fi.zerlege("source:graylog AND cue01") == ("source:graylog AND", "cue01")
    assert fi.zerlege("source:graylog ") == ("source:graylog", "")
    assert fi.zerlege('title:"disk') == ('title:"disk', "")          # offene Phrase: nichts ergänzen
    assert fi._ohne_operator("source:graylog AND") == "source:graylog"
    assert fi._regex_praefix("Cue.0") == "[cC][uU][eE]\\.0.*"


class _OSV(_OS):
    def __init__(self, aggs, totals, mapping=None):
        super().__init__(antwort={"hits": {"total": {"value": 0}}, "aggregations": aggs}, mapping=mapping)
        self.totals, self.msearch_body = totals, None

    async def msearch(self, body):
        self.msearch_body = body
        return {"responses": [{"hits": {"total": {"value": n}}} for n in self.totals]}

    async def get_mapping(self, index):
        return {"cs-feed-graylog": {"mappings": {"properties": {"metadata": {"properties": {"container_name": {}}}}}}}


def test_vorschlaege_sind_suchen_mit_echter_trefferzahl(monkeypatch):
    fi._feldnamen_cache.clear()
    os = _OSV({"f0": {"buckets": [{"key": "cue0113.example.com", "doc_count": 40}]},
               "f6": {"buckets": [{"key": "cue-sync", "doc_count": 3}]}}, totals=[40, 3])
    _setze(monkeypatch, os)
    r = asyncio.run(fi.vorschlaege("severity:high AND cue", since_seconds=3600))
    assert [(v["q"], v["anzahl"]) for v in r["vorschlaege"]] == [
        ("severity:high AND metadata.host.keyword:cue0113.example.com", 40),
        ("severity:high AND tags:cue\\-sync", 3)]
    # Kandidaten aus dem Ergebnis der Basis (ohne angehängtes AND), Präfix ohne Groß-/Kleinschreibung
    b = os.koerper[0]
    assert b["query"]["bool"]["must"][0]["query_string"]["query"] == "severity:high"
    assert b["aggs"]["f0"]["terms"] == {"field": "metadata.host.keyword", "size": 9, "include": "[cC][uU][eE].*",
                                        "execution_hint": "map"}
    # gezählt wird die vollständige Suche jedes Vorschlags
    assert os.msearch_body[1]["query"]["bool"]["must"][0]["query_string"]["query"] == \
        "severity:high AND metadata.host.keyword:cue0113.example.com"


def test_vorschlaege_feldnamen_und_werte_eines_felds(monkeypatch):
    fi._feldnamen_cache.clear()
    os = _OSV({}, totals=[])
    _setze(monkeypatch, os)
    r = asyncio.run(fi.vorschlaege("metadata.cont"))
    assert [v["q"] for v in r["vorschlaege"]] == ["metadata.container_name:"]
    os = _OSV({"f0": {"buckets": [{"key": "graylog", "doc_count": 9}, {"key": "wazuh", "doc_count": 0}]}},
              totals=[9, 0], mapping=_mapping(source="keyword"))
    _setze(monkeypatch, os)
    r = asyncio.run(fi.vorschlaege("source:"))
    assert [(v["q"], v["anzahl"]) for v in r["vorschlaege"]] == [("source:graylog", 9)]   # 0 Treffer entfällt
    _setze(monkeypatch, _OSV({}, totals=[], mapping=_mapping(body="text")))
    assert "Freitext" in asyncio.run(fi.vorschlaege("body:x"))["fehler"]


def test_teilfehler_der_shards_ist_ein_fehler(monkeypatch):
    """Gemessen 07.11.: mit Zeitraum kommt `source:(` als HTTP 200, 0 Treffer, 4 Shards gescheitert."""
    _setze(monkeypatch, _OS(antwort={"hits": {"total": {"value": 0}, "hits": []}, "_shards": {
        "total": 7, "failed": 4, "failures": [{"reason": {"type": "query_shard_exception",
                                                         "reason": "Failed to parse query [source:(]"}}]}}))
    with pytest.raises(fi.FeedQueryFehler, match="Failed to parse"):
        asyncio.run(fi.suche_seite("cs-feed-*", "source:(", since_seconds=86400))


def test_leerer_wert_ist_nicht_fehlendes_feld():
    assert fi.escape_wert("") == '""'
