"""Konsolen-Gedächtnis: nur Sitzungen mit Bezug, nur Mensch- und KI-Text, Bezug als Hashtag,
Herkunft im Host-Objekt benannt."""
import asyncio
from datetime import datetime, timezone

from app.services.log_watch import gespraech, hostobjekt, konsole


def test_bezug_nur_mit_incident_alert_oder_ticket():
    assert konsole.bezug(None, None) == []
    assert konsole.bezug("incident:abc", None) == ["incident:abc"]
    assert konsole.bezug("glog:da8b", "IMIT-1") == ["alert:glog:da8b", "ticket:IMIT-1"]


def test_verlauf_nur_mensch_und_ki_text():
    historie = [
        {"role": "system", "content": "geheimer Systemprompt"},
        {"role": "user", "content": "Warum ist cue0113 rot?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Platte voll."},
                                          {"type": "tool_use", "name": "shell"}]},
        {"role": "tool", "content": "df -h …"},
        {"role": "assistant", "content": "   "},
    ]
    assert konsole.verlauf_aus_historie(historie) == [
        {"rolle": "mensch", "text": "Warum ist cue0113 rot?"},
        {"rolle": "ki", "text": "Platte voll."},
    ]


def test_zuege_tragen_herkunft_bezug_und_hosts():
    jetzt = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
    z = gespraech.zuege("sid-1", {"start": "incident:abc", "hosts": ["cue0113.example.com"]},
                        [konsole.HERKUNFT, "incident:abc"],
                        konsole.verlauf_aus_historie([{"role": "user", "content": "x"}]), jetzt)
    assert z[0]["fall"] == "incident:abc" and z[0]["rolle"] == "mensch"
    assert z[0]["hashtags"] == ["host:cue0113.example.com", "incident:abc", "quelle:konsole"]


def test_sichere_nur_mit_bezug(monkeypatch):
    geschrieben, geholt = [], []

    async def holer():
        geholt.append(1)
        return [{"role": "user", "content": "Warum?"}, {"role": "assistant", "content": "Platte voll."}]

    async def schreibe(z):
        geschrieben[:] = z
        return len(z)

    monkeypatch.setattr(gespraech, "schreibe", schreibe)

    async def ohne(sid, uid):
        return [], []
    monkeypatch.setattr(konsole, "_bezug_der_sitzung", ohne)
    assert asyncio.run(konsole.sichere("sid", "u", holer)) == 0
    assert geholt == []   # ohne Bezug wird der Container gar nicht erst gefragt

    async def mit(sid, uid):
        return ["ticket:IMIT-1"], ["cue0113.example.com"]
    monkeypatch.setattr(konsole, "_bezug_der_sitzung", mit)
    assert asyncio.run(konsole.sichere("sid", "u", holer)) == 2
    assert [(z["sitzung"], z["zug"], z["rolle"]) for z in geschrieben] == [("sid", 0, "mensch"), ("sid", 1, "ki")]
    assert geschrieben[1]["hashtags"] == ["host:cue0113.example.com", "quelle:konsole", "ticket:IMIT-1"]


def test_gedaechtnis_benennt_herkunft():
    sitzungen = [{"sitzung": "s1", "zuege": [
        {"rolle": "mensch", "text": "Warum?", "fall": "incident:abc", "hashtags": ["quelle:konsole"]},
        {"rolle": "ki", "text": "Platte voll.", "fall": "incident:abc", "hashtags": ["quelle:konsole"]}]},
        {"sitzung": "s2", "zuege": [{"rolle": "ki", "text": "OOM", "fall": "muster:1", "hashtags": []}]}]
    g = hostobjekt.kuerze_gedaechtnis([], sitzungen)["gespraeche"]
    assert [(s["herkunft"], s["bezug"]) for s in g] == [("konsole", "incident:abc"), ("ki-analyse", "muster:1")]


def test_werkzeugantwort_in_der_ansicht_der_lage():
    """lw_suche: höchstens JE_ANTWORT Treffer mit Host und zitierbarem Teiltext, Facetten nie
    abgeschnitten, Kürzung benannt; Fehler unverändert."""
    lang = "Platte voll " * 100
    erg = {"filter": "host:a", "gesamt": 30, "treffer": [
        {"id": f"graylog:{i}", "zeit": "z", "quelle": "graylog", "host": "a", "relevanz": 3,
         "gruende": ["R5"], "hashtags": ["thema:disk-full", "host:a", "ort:MUE-0"], "text": lang}
        for i in range(30)], "facetten": {"thema": [{"wert": "disk-full", "anzahl": 30}],
                                          "host": [{"wert": "a", "anzahl": 30}]}}
    k = hostobjekt.kuerze_antwort("lw_suche", erg)
    assert k["gesamt"] == 30 and k["gezeigt"] == hostobjekt.JE_ANTWORT == len(k["treffer"])
    t = k["treffer"][0]
    assert t["host"] == "a" and t["hashtags"] == ["thema:disk-full"]
    assert len(t["text"]) == hostobjekt.TEXT_MAX and lang.startswith(t["text"])   # Teilstring → zitierbar
    assert k["facetten"] == {"thema": ["disk-full (30)"]} and "lw_nachricht" in k["hinweis"]
    f = hostobjekt.kuerze_antwort("lw_facetten", {"gesamt": 30, "facetten": erg["facetten"]})
    assert f["facetten"] == {"thema": ["disk-full (30)"], "host": ["a (30)"]}   # alle Arten
    assert hostobjekt.kuerze_antwort("lw_suche", {"fehler": "x"}) == {"fehler": "x"}


def test_trichter_kuerzt_vor_der_grenze():
    from app.services.log_watch import trichter
    erg = {"gesamt": 1, "treffer": [{"id": "g:1", "text": "x" * 50000, "hashtags": []}], "facetten": {}}
    s = trichter._kurz("lw_suche", erg)
    assert len(s) < trichter.ANTWORT_MAX and "abgeschnitten" not in s
