"""Lernen: ein Fehler, den die KI schon untersucht hat, geht nicht noch einmal an die KI
(Nutzer, 07.11.) — außer er bringt ein neues Kriterium mit (Plan, Vorrang N2). Ein bekannter
Fehler ist ein Objekt: Filter in der Filtersprache, Host, Hashtags."""
from datetime import datetime, timezone

from app.services.log_watch import bekannt as bk
from app.services.log_watch import filter as flt
from app.services.log_watch.regelwerk import passt

JETZT = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _n(i, schluessel, host="a", kriterien=("R1",), tags=(), **kw):
    return {"_id": f"g:{i}", "_index": "cs-logs-2026.11.07", "schluessel": schluessel, "host": host,
            "kriterien": list(kriterien), "hashtags": [schluessel, f"host:{host}", *tags],
            "zeit": f"2026-11-07T11:{i:02d}:00+00:00", **kw}


def test_derselbe_fehler():
    assert bk.fehler({"schluessel": "muster:m1", "host": "a"}) == "muster:m1"     # über alle Hosts
    assert bk.fehler({"schluessel": "dienst:Filesystem /", "host": "a"}) == "a|dienst:Filesystem /"
    assert bk.fehler({"schluessel": "regel:5710", "host": "b"}) == "b|regel:5710"
    assert bk.fehler({"host": "a"}) is None


def test_bekannt_geht_an_den_befund_nicht_an_die_ki():
    e = {"befund": "ki:abc", "relevanz": 1, "status": "aktiv", "merkmale": ["R1"]}
    offen = [_n(1, "muster:m1", "a"), _n(2, "muster:m1", "neu-host"), _n(3, "muster:m2")]
    ki, haengen = bk.teile(offen, {"muster:m1": e})
    assert [n["_id"] for n in ki] == ["g:3"]
    assert [(h["id"], h["befund"], h["relevanz"], h["host"]) for h in haengen] == [
        ("g:1", "ki:abc", 1, "a"), ("g:2", "ki:abc", 1, "neu-host")]


def test_neues_kriterium_geht_wieder_an_die_ki():
    e = {"befund": "ki:abc", "status": "aktiv", "merkmale": ["R1"]}
    ki, haengen = bk.teile([_n(1, "muster:m1", kriterien=("R1", "R5")),
                            _n(2, "muster:m1", tags=("signal:dringend",))], {"muster:m1": e})
    assert haengen == []
    assert [(n["_id"], n["neu_seit_befund"], n["bekannt_als"]) for n in ki] == [
        ("g:1", ["R5"], "ki:abc"), ("g:2", ["dringend"], "ki:abc")]


def test_vom_menschen_widerlegt_geht_wieder_an_die_ki():
    e = {"befund": "ki:abc", "status": "widerlegt", "merkmale": ["R1"]}
    ki, haengen = bk.teile([_n(1, "muster:m1")], {"muster:m1": e})
    assert [n["_id"] for n in ki] == ["g:1"] and haengen == []


def test_eintrag_ist_ein_objekt_mit_filter_host_und_hashtags():
    gesehen = [_n(1, "dienst:Filesystem /", "cue0113", tags=("thema:disk-full", "befund:alt", "signal:neu")),
               _n(2, "muster:m1", "a", kriterien=("R2",)), _n(3, "muster:m1", "b", kriterien=("R4",))]
    alt = {"muster:m1": {"merkmale": ["R1"], "wiederholungen": 7, "erstmals": "2026-11-06T00:00:00+00:00",
                         "hosts": ["c"], "hashtags": ["thema:oom"]}}
    e = {x["fehler"]: x for x in bk.eintraege("ki:neu", "Platte voll", 2, gesehen, alt, JETZT)}
    d = e["cue0113|dienst:Filesystem /"]
    assert d["filter"] == 'host:cue0113 dienst:"Filesystem /"' and d["host"] == "cue0113"
    assert d["hashtags"] == ["dienst:Filesystem /", "host:cue0113", "thema:disk-full"]   # ohne befund:/signal:
    assert d["status"] == "aktiv" and d["herkunft"] == "ki" and d["wiederholungen"] == 0
    m = e["muster:m1"]
    assert m["filter"] == "muster:m1" and m["host"] == ""                              # über alle Hosts
    assert m["befund"] == "ki:neu" and m["merkmale"] == ["R1", "R2", "R4"]             # Folge-Befund, vereinigt
    assert m["wiederholungen"] == 7 and m["erstmals"] == "2026-11-06T00:00:00+00:00" and m["hosts"] == ["a", "b", "c"]
    assert "thema:oom" in m["hashtags"]


def test_filter_trifft_genau_den_fehler():
    """Schlüssel und Filter treffen dieselben Nachrichten (gemessen 07.11.: der Gruppenschlüssel steht
    an allen 7 677 Nachrichten der Historie auch als Hashtag)."""
    nachrichten = [_n(i, s, h) for i, (s, h) in enumerate([
        ("muster:m1", "a"), ("muster:m1", "b"), ("muster:m2", "a"), ("dienst:Filesystem /", "a"),
        ("dienst:Filesystem /", "b"), ("dienst:Filesystem /var", "a"), ("regel:5710", "a"),
        ("dienst:Check_MK*", "a"), ("dienst:Check_MK", "a"), ("bedrohung:x (y)", "a")])]
    for ziel in nachrichten:
        f = bk.fehler(ziel)
        b = flt.bedingung(bk.filter_von(ziel["schluessel"], "" if f.startswith("muster:") else ziel["host"]))
        for n in nachrichten:
            assert passt(b, set(n["hashtags"]), {}) == (bk.fehler(n) == f), (f, n["schluessel"], n["host"])


def test_gesehen_belege_immer_lage_nur_ungebunden():
    docs = {"g:1": {"schluessel": "muster:m1", "hashtags": ["befund:alt"]},
            "g:2": {"schluessel": "muster:m2", "hashtags": ["befund:alt"]},
            "g:3": {"schluessel": "muster:m3", "hashtags": []},
            "g:4": {"schluessel": "muster:m4", "hashtags": ["befund:neu"]},
            "ki:x": {"quelle": "ki", "hashtags": []}}
    g = bk.gesehen_aus(docs, ["g:1", "ki:x"], ["g:2", "g:3", "g:4", "fehlt"], "ki:neu")
    assert [d["schluessel"] for d in g] == ["muster:m1", "muster:m3", "muster:m4"]
