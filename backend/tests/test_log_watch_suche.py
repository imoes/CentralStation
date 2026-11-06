"""Eine Suche über die Historie: Filter + Zeitraum → Query, Facetten je Hashtag-Art."""
from datetime import datetime, timezone

from app.services.log_watch import suche as s

SEIT, BIS = datetime(2026, 11, 6, 20, tzinfo=timezone.utc), datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


def test_leerer_filter_ist_der_ganze_zeitraum():
    assert s.abfrage("", None, SEIT, BIS) == {"bool": {"filter": [
        {"range": {"zeit": {"gte": "2026-11-06T20:00:00+00:00", "lt": "2026-11-06T21:00:00+00:00"}}}]}}


def test_filter_und_objekt_werden_aufgeloest():
    q = s.abfrage("@Prod thema:oom", {"Prod": "kritikalitaet:prod"}, SEIT, BIS)
    assert q["bool"]["must"] == [{"bool": {"filter": [{"term": {"hashtags": "kritikalitaet:prod"}},
                                                      {"term": {"hashtags": "thema:oom"}}], "must": []}}]


def test_facetten_je_art():
    f = s.facetten([{"key": "host:a", "doc_count": 5}, {"key": "thema:oom", "doc_count": 3},
                    {"key": "host:b", "doc_count": 2}])
    assert f == {"host": [{"wert": "a", "hashtag": "host:a", "anzahl": 5},
                          {"wert": "b", "hashtag": "host:b", "anzahl": 2}],
                 "thema": [{"wert": "oom", "hashtag": "thema:oom", "anzahl": 3}]}


def test_unlesbarer_filter_nennt_den_grund(monkeypatch):
    import asyncio
    erg = asyncio.run(s.suche("(thema:oom"))
    assert "fehler" in erg and "fehlt" in erg["fehler"]


def test_netzplan_knoten_je_art_begrenzt_und_fokus_immer_dabei():
    b = ([{"key": f"host:h{i}", "doc_count": 100 - i} for i in range(8)]
         + [{"key": "muster:abc", "doc_count": 99}, {"key": "thema:oom", "doc_count": 3},
            {"key": "host:selten", "doc_count": 1}])
    k = s.knoten_waehlen(b, 15, {"host:selten"})
    ids = [x["id"] for x in k]
    assert ids == ["host:h0", "host:h1", "host:h2", "host:h3", "host:h4", "thema:oom", "host:selten"]
    assert "muster:abc" not in ids and k[-1]["fokus"] is True


def test_netzplan_kanten_aus_adjacency_matrix():
    ids = ["host:a", "thema:oom", "quelle:graylog"]
    adj = [{"key": "n0", "doc_count": 5}, {"key": "n0&n1", "doc_count": 3},
           {"key": "n1&n2", "doc_count": 7}, {"key": "n0&n2", "doc_count": 0}]
    assert s.kanten(adj, ids) == [{"a": "thema:oom", "b": "quelle:graylog", "anzahl": 7},
                                  {"a": "host:a", "b": "thema:oom", "anzahl": 3}]


def test_fokus_aus_dem_filter():
    assert s._fokus("host:a (thema:oom OR thema:dns) !signal:stumm") == {"host:a", "thema:oom", "thema:dns"}
    assert s._fokus("(kaputt") == set()


def test_netzplan_naben_fallen_weg_ausser_im_fokus():
    b = [{"key": "netz:intern", "doc_count": 90}, {"key": "host:a", "doc_count": 30}, {"key": "thema:oom", "doc_count": 10}]
    assert [x["id"] for x in s.knoten_waehlen(b, 15, set(), gesamt=100)] == ["host:a", "thema:oom"]
    assert [x["id"] for x in s.knoten_waehlen(b, 15, {"netz:intern"}, gesamt=100)][0] == "netz:intern"
