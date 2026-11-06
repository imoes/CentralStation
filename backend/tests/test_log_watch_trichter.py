"""KI-Trichter: Schleife mit Fake-KI — Schritte festgehalten, Ablehnung geht zurück an die KI,
Ende ohne Befund ist benannt, der Systemprompt ist statisch."""
import asyncio
import re

from app.services.llm_client import Werkzeugantwort
from app.services.log_watch import filter as flt
from app.services.log_watch import trichter as tr


def _aufruf(name, **arg):
    return {"id": f"c-{name}", "name": name, "argumente": arg, "fehler": None}


class _FakeKI:
    def __init__(self, folge):
        self.folge = list(folge)
        self.gesehen = []

    async def __call__(self, cfg, nachrichten, tools):
        self.gesehen.append([m.get("content") for m in nachrichten if m["role"] == "tool"])
        aufrufe = self.folge.pop(0)
        return Werkzeugantwort(text="", aufrufe=aufrufe,
                               nachricht={"role": "assistant", "content": "", "tool_calls": []})


def _rufe(monkeypatch, antworten):
    async def rufe(name, argumente, **intern):
        return antworten[name].pop(0) if isinstance(antworten[name], list) else antworten[name]
    monkeypatch.setattr(tr.wz, "rufe", rufe)


def test_trichter_bis_befund(monkeypatch):
    _rufe(monkeypatch, {"lw_wissen": {"gesamt": 0}, "lw_facetten": {"gesamt": 7},
                        "lw_befund": [{"abgelehnt": "kein geprüfter Beleg — x: Zitat steht nicht wörtlich"},
                                      {"id": "ki:abc", "belege": 2, "verworfen": []}]})
    ki = _FakeKI([[_aufruf("lw_wissen", filter="host:a")], [_aufruf("lw_facetten", filter="host:a")],
                  [_aufruf("lw_befund", titel="t")], [_aufruf("lw_befund", titel="t2")]])
    erg = asyncio.run(tr.analysiere({"start": "host:a", "anzahl": 7, "hosts": ["a"]}, object(), ki))
    assert erg["befund"] == "ki:abc"
    assert [s["werkzeug"] for s in erg["schritte"]] == ["lw_wissen", "lw_facetten", "lw_befund", "lw_befund"]
    assert erg["schritte"][2]["fehler"].startswith("kein geprüfter Beleg")
    assert "nicht wörtlich" in ki.gesehen[3][-1]          # Ablehnung ging an die KI zurück


def test_ohne_befund_benannt(monkeypatch):
    ki = _FakeKI([[], []])
    erg = asyncio.run(tr.analysiere({"start": "host:a", "anzahl": 1, "hosts": ["a"]}, object(), ki))
    assert erg["kein_befund"] == "KI hat ohne lw_befund geendet"


def test_systemprompt_statisch_und_ausloeser_lesbar():
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\{|\}", tr.SYSTEM)
    q = flt.opensearch(tr.AUSLOESER)
    assert {"range": {"relevanz": {"gte": 3.0}}} in q["bool"]["filter"][0]["bool"]["should"]


def test_offene_eine_analyse_hat_gesehen_was_ihr_start_bis_dahin_traf():
    n = [{"host": "a", "zeit": "2026-11-06T21:19:06Z", "hashtags": ["host:a", "muster:m1"]},
         {"host": "b", "zeit": "2026-11-06T21:19:07Z", "hashtags": ["host:b", "muster:m1"]},
         {"host": "a", "zeit": "2026-11-06T21:40:00Z", "hashtags": ["host:a", "muster:m1"]},   # nach dem Befund
         {"host": "c", "zeit": "2026-11-06T21:01:00Z", "hashtags": ["host:c"]}]
    befunde = [{"host": "a", "zeit": "2026-11-06T21:33:45Z", "ai_provenance": {"start": "muster:m1"}},
               {"host": "c", "zeit": "2026-11-06T21:24:21Z"}]          # alt, ohne Start → host:c
    assert [x["zeit"][11:19] for x in tr.offene(n, befunde)] == ["21:40:00"]


def test_faelle_gleiches_muster_ein_fall_ueber_hosts():
    n = [{"host": "cue0174", "hashtags": ["muster:m1"]}, {"host": "cue222", "hashtags": ["muster:m1"]},
         {"host": "cue222", "hashtags": ["muster:m1"]}, {"host": "srv032", "hashtags": ["host:srv032"]}]
    assert tr.faelle(n) == [{"start": "muster:m1", "anzahl": 3, "hosts": ["cue0174", "cue222"]},
                            {"start": "host:srv032", "anzahl": 1, "hosts": ["srv032"]}]
