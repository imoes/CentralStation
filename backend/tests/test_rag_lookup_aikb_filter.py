"""rag_lookup fragt die AIKB per Hashtag-Filter statt über die LLM-Vorstufe (Nutzer 09.11.2026)."""
import asyncio
import json

import httpx

from app.services.ai_agent.aikb_filter import aikb_filter_aus_alarmen
from app.services.connectors.aikb import AIKBConnector
from app.services.log_watch.hosts import Hostverzeichnis

VERZ = Hostverzeichnis.aus_checkmk([
    {"hostname": "cue0109.example.com", "metadata": {}},
    {"hostname": "docker0218.example.com", "metadata": {}},
    {"hostname": "web01.example.com", "metadata": {}},
    {"hostname": "web01.example.net", "metadata": {}},
])


def test_filter_aus_zugeordneten_hosts():
    f, why = aikb_filter_aus_alarmen([{"host": "CUE0109"}, {"agent": "docker0218"}, {"host": "cue0109.example.com"},
                                      {"host": "web01"}], VERZ)
    assert f == "host:(cue0109.example.com OR docker0218.example.com) !art:vorlage"
    assert "2 Host(s)" in why and "web01 (mehrdeutig)" in why


def test_ein_host_ohne_klammer():
    assert aikb_filter_aus_alarmen([{"host": "cue0109"}], VERZ)[0] == "host:cue0109.example.com !art:vorlage"


def test_ohne_host_themen_aus_dem_vokabular():
    f, why = aikb_filter_aus_alarmen([{"host": "gibtsnicht01", "title": "write /data/x: no space left on device"}], VERZ)
    assert f == "thema:disk-full !art:vorlage" and "gibtsnicht01 (unbekannt)" in why


def test_weder_host_noch_thema_heisst_rueckfall():
    f, why = aikb_filter_aus_alarmen([{"title": "irgendwas"}], VERZ)
    assert f is None and why.startswith("weder")


def _connector(monkeypatch, handler):
    c = AIKBConnector(base_url="http://aikb", credentials={"api_token": "aikb_x"})
    real = httpx.AsyncClient
    monkeypatch.setattr(c, "_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return c


def test_search_filter_vertrag(monkeypatch):
    gesehen = {}

    def h(req):
        gesehen.update(json.loads(req.content), auth=req.headers["authorization"], pfad=req.url.path)
        return httpx.Response(200, json={"treffer": [{"titel": "cue0109 Oracle", "auszug": "Tablespace …", "url": "u",
                                                      "space": "IT", "hashtags": ["host:cue0109.example.com"],
                                                      "abschnitt": "Betrieb"}],
                                         "hinweise": ["dienst: noch nicht vergeben"], "dauer": {"gesamt_ms": 31}})
    r = asyncio.run(_connector(monkeypatch, h).search_filter("host:cue0109.example.com !art:vorlage", size=6))
    assert gesehen == {"filter": "host:cue0109.example.com !art:vorlage", "size": 6, "auth": "Bearer aikb_x",
                       "pfad": "/search/filter"}
    t = r["treffer"][0]
    assert t["title"] == "cue0109 Oracle" and t["content"] == "Tablespace …" and t["hashtags"] == ["host:cue0109.example.com"]
    assert r["fehlt"] is False and r["dauer"] == {"gesamt_ms": 31}


def test_search_filter_endpunkt_fehlt_ist_benannt(monkeypatch):
    c = _connector(monkeypatch, lambda req: httpx.Response(404, json={"detail": "Not Found"}))
    r = asyncio.run(c.search_filter("host:x"))
    assert r["fehlt"] is True and "fehlt" in c.last_error


def _lauf(monkeypatch, treffer, last_error=None, searxng=False):
    from app.services.ai_agent import graph
    aufrufe = {"llm": 0, "opensearch": 0}

    class FakeAikb:
        def __init__(self): self.last_error = None
        async def search_filter(self, f, size=10):
            self.last_error = last_error
            return {"treffer": treffer, "hinweise": [], "dauer": {}, "fehlt": last_error is not None}
        async def search_opensearch(self, q, size=4):
            aufrufe["opensearch"] += 1
            return [{"title": "alt", "content": "x"}]

    async def _conn(db): return FakeAikb()
    async def _verz(db): return VERZ
    async def _llm(*a, **k):
        aufrufe["llm"] += 1
        return json.dumps({"needs_rag": True, "queries": ["cue0109 tablespace"], "deepsearch": False})
    monkeypatch.setattr(graph, "_aikb_connector", _conn)
    monkeypatch.setattr("app.services.log_watch.hosts.verzeichnis", _verz)
    monkeypatch.setattr(graph, "generate_text", _llm)

    class Sx:
        is_configured = searxng
    state = {"enriched_alerts": [{"host": "cue0109", "severity": "warning", "source": "checkmk", "title": "Tablespace"}]}
    out = asyncio.run(graph.rag_lookup(state, db=None, llm_config=None, searxng_config=Sx()))
    return out["rag_context"], aufrufe


def test_rag_lookup_filter_treffer_ohne_llm(monkeypatch):
    ctx, n = _lauf(monkeypatch, [{"title": "cue0109 Oracle", "content": "Tablespace"}])
    assert n == {"llm": 0, "opensearch": 0}
    assert ctx == [{"source": "server-kb", "query": "host:cue0109.example.com !art:vorlage",
                    "results": [{"title": "cue0109 Oracle", "content": "Tablespace"}]}]


def test_rag_lookup_nichts_dokumentiert_ist_eine_antwort(monkeypatch):
    ctx, n = _lauf(monkeypatch, [])
    assert n == {"llm": 0, "opensearch": 0} and ctx == []


def test_rag_lookup_rueckfall_wenn_endpunkt_fehlt(monkeypatch):
    ctx, n = _lauf(monkeypatch, [], last_error="/search/filter fehlt auf dieser AIKB (HTTP 404)")
    assert n == {"llm": 1, "opensearch": 1}
    assert ctx[-1]["query"] == "cue0109 tablespace"
