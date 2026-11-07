"""Schreibweg der Historie: eine Kontingent-Entscheidung für Zählung und Speicherung, und je
Quelle das Nachholen der vollständigen Nachricht (Graylog/Firewall/Switch über IDs, CheckMK
als Problem selbst)."""
import asyncio
from datetime import datetime, timezone

from app.services.log_watch import lauf
from app.services.log_watch.raster import Kontingent, Treffer, zaehle

B = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)


def _t(anzahl, urteil="relevant", belege=(), quelle="graylog", schluessel="muster:k1", host="a"):
    return Treffer(quelle, schluessel, host, 2 if urteil == "relevant" else 0, [], [f"host:{host}"],
                   anzahl, "beispiel", urteil=urteil, kriterien=["R5"], belege=list(belege))


def test_kontingent_eine_entscheidung_fuer_zaehlung_und_speicherung():
    k = Kontingent(5)
    a, b, c = _t(3), _t(4), _t(9, urteil="nicht_relevant")
    w = zaehle([a, b, c], k, B)
    assert (a.kontingent_n, b.kontingent_n, c.kontingent_n) == (3, 2, 0)   # Stunde voll nach 5
    assert w["gespeichert_waere"] == a.kontingent_n + b.kontingent_n


class _FakeGraylog:
    def __init__(self, nachrichten):
        self.nachrichten = {m["_id"]: m for m in nachrichten}
        self.abfragen = []

    async def search_window(self, query, since, until, limit=10_000, **_):
        self.abfragen.append((query, limit))
        ids = [i.strip('"') for i in query[len("_id:("):-1].split(" OR ")]
        return [self.nachrichten[i] for i in ids if i in self.nachrichten], 0


def test_graylog_holer_genau_kontingent_n_mit_allen_feldern():
    gl = _FakeGraylog([
        {"_id": "m1", "timestamp": "2026-11-06T20:01:00.000Z", "message": "OOM 1", "level": 3,
         "gl2_source_input": "x", "streams": ["s"], "container_name": "web"},
        {"_id": "m2", "timestamp": "2026-11-06T20:02:00.000Z", "message": "OOM 2", "level": 3},
    ])
    t = _t(7, belege=[("m1", ""), ("m2", ""), ("m3", "")])
    out = asyncio.run(lauf._graylog_holer(gl, B)([(t, 2)]))
    assert [qid for _, qid, *_ in out] == ["m1", "m2"]                      # nur n=2, m3 nicht gefragt
    assert gl.abfragen == [('_id:("m1" OR "m2")', 2)]
    _, _, zeit, text, roh = out[0]
    assert zeit == datetime(2026, 11, 6, 20, 1, tzinfo=timezone.utc) and text == "OOM 1"
    assert roh["container_name"] == "web" and "gl2_source_input" not in roh and "streams" not in roh


def test_graylog_holer_fehlende_nachricht_wird_nicht_erfunden():
    gl = _FakeGraylog([])
    t = _t(1, belege=[("weg", "")])
    assert asyncio.run(lauf._graylog_holer(gl, B)([(t, 1)])) == []


def test_graylog_holer_stueckelt_zu_50_ids():
    msgs = [{"_id": f"m{i}", "timestamp": "2026-11-06T20:00:00Z", "message": "x"} for i in range(120)]
    gl = _FakeGraylog(msgs)
    paare = [(_t(5, belege=[(f"m{i}", "") for i in range(j, j + 5)]), 5) for j in range(0, 120, 5)]
    out = asyncio.run(lauf._graylog_holer(gl, B)(paare))
    assert len(out) == 120 and [n for _, n in gl.abfragen] == [50, 50, 20]


def test_checkmk_holer_problem_ist_die_nachricht():
    probleme = [{"host": "vpp0221", "service": "Filesystem /", "state": 2, "output": "98% used",
                 "last_state_change": 1793995200, "metadata": {"labels": {"x": "y"}}}]
    t = _t(1, quelle="checkmk", schluessel="dienst:Filesystem /", host="vpp0221.example.com")
    out = asyncio.run(lauf._checkmk_holer(probleme, B)([(t, 1)]))
    (_, qid, zeit, text, roh), = out
    assert qid == "vpp0221|Filesystem /|1793995200" and text == "98% used"
    assert zeit == datetime.fromtimestamp(1793995200, tz=timezone.utc) and roh["state"] == 2


def test_nachbetten_ergaenzt_nur_dokumente_ohne_vektor(monkeypatch):
    from app.services.log_watch import embed, historie

    gesucht, geschrieben = {}, {}

    class _OS:
        async def search(self, index, body, **_):
            gesucht.update(index=index, query=body["query"])
            return {"hits": {"total": {"value": 3}, "hits": [
                {"_index": "cs-logs-2026.11.06", "_id": "graylog:m1", "_source": {"text": "OOM"}},
                {"_index": "cs-logs-2026.11.06", "_id": "graylog:m2", "_source": {"text": "Disk"}}]}}

        async def bulk(self, body):
            geschrieben["body"] = body
            return {"items": [{"update": {}}, {"update": {"error": "x"}}]}

    async def _einbetten(texte):
        return [[0.5] * embed.DIM for _ in texte]

    import app.core.opensearch as core_os
    monkeypatch.setattr(core_os, "get_opensearch", lambda: _OS())
    monkeypatch.setattr(embed, "einbetten", _einbetten)
    erg = asyncio.run(historie.nachbetten(limit=2))
    assert gesucht["query"] == {"bool": {"must_not": [{"exists": {"field": "vektor"}}]}}
    assert erg == {"offen": 3, "nachgebettet": 1, "abgelehnt": 1, "nicht_einbettbar": 0}
    assert geschrieben["body"][0] == {"update": {"_index": "cs-logs-2026.11.06", "_id": "graylog:m1"}}
    assert len(geschrieben["body"][1]["doc"]["vektor"]) == embed.DIM


def test_wazuh_holer_holt_genau_die_mitgelieferten_ids():
    from app.services.log_watch.raster import wazuh_zeilen
    aggs = {"regel": {"buckets": [{"key": "5710", "info": {"hits": {"hits": [{"_source": {"rule": {
        "level": 5, "description": "sshd: Attempt to login using a non-existent user"}}}]}},
        "agent": {"buckets": [
            {"key": "web01", "doc_count": 9, "belege": {"hits": {"hits": [
                {"_id": "a1", "_index": "wazuh-alerts-4.x-2026.11.06"}, {"_id": "a2", "_index": "x"}]}}},
            {"key": "db01", "doc_count": 1, "belege": {"hits": {"hits": [{"_id": "b1", "_index": "x"}]}}}]}}]}}
    zeilen = wazuh_zeilen(aggs)
    assert zeilen[0]["belege"] == [("a1", "wazuh-alerts-4.x-2026.11.06"), ("a2", "x")]

    abfragen = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _W:
        def _client(self, timeout):
            return _Client()

        async def _query_indexer(self, client, index, body):
            abfragen.append(body)
            return {"hits": {"hits": [{"_id": "a1", "_source": {"timestamp": "2026-11-06T20:01:00Z",
                                                                "full_log": "Invalid user x", "agent": {"name": "web01"}}}]}}

    t1 = _t(9, quelle="wazuh", belege=zeilen[0]["belege"])
    t2 = _t(1, quelle="wazuh", belege=zeilen[1]["belege"])
    out = asyncio.run(lauf._wazuh_holer(_W(), B)([(t1, 1), (t2, 1)]))
    assert abfragen == [{"size": 2, "query": {"ids": {"values": ["a1", "b1"]}}}]   # n je Treffer
    assert [(qid, text) for _, qid, _, text, _ in out] == [("a1", "Invalid user x")]   # b1 fehlt → nicht erfunden


def test_nachbetten_ein_giftiges_dokument_blockiert_die_anderen_nicht(monkeypatch):
    """Regression 07.11.: ein zu langer Text ließ den ganzen Stapel scheitern, bei jedem Lauf."""
    from app.services.log_watch import embed, historie

    class _OS:
        async def search(self, index, body, **_):
            return {"hits": {"total": {"value": 3}, "hits": [
                {"_index": "i", "_id": "kurz1", "_source": {"text": "kurz"}},
                {"_index": "i", "_id": "gift", "_source": {"text": "GIFT"}},
                {"_index": "i", "_id": "kurz2", "_source": {"text": "auch kurz"}}]}}

        async def bulk(self, body):
            return {"items": [{"update": {}}] * (len(body) // 2)}

    async def _einbetten(texte):
        if "GIFT" in texte:
            raise embed.EmbeddingFehler("HTTPStatusError: 500")
        return [[0.1] * embed.DIM for _ in texte]

    import app.core.opensearch as core_os
    monkeypatch.setattr(core_os, "get_opensearch", lambda: _OS())
    monkeypatch.setattr(embed, "einbetten", _einbetten)
    erg = asyncio.run(historie.nachbetten(limit=3))
    assert erg["nachgebettet"] == 2 and erg["nicht_einbettbar"] == 1 and erg["nicht_einbettbar_ids"] == ["gift"]


def test_einbetten_kuerzt_unter_die_server_batchgroesse():
    from app.services.log_watch import embed
    assert embed.MAX_ZEICHEN / 1.85 < 2048          # gemessen: 1,85 Zeichen je Token Logtext
