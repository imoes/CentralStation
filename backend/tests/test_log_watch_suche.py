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
