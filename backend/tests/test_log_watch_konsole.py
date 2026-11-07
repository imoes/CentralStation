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
