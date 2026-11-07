"""KI-Trichter: Schleife mit Fake-KI — Schritte festgehalten, Ablehnung geht zurück an die KI,
Ende ohne Befund ist benannt, der Systemprompt ist statisch."""
import asyncio
import re

from app.services.llm_client import Werkzeugantwort
from app.services.log_watch import filter as flt
from app.services.log_watch import trichter as tr


PROTOKOLL: list = []


async def _bauer(host, stunden):
    return {"host": host, "felder": {"name": host, "ort": "MUE-0", "kritikalitaet": "prod", "netz": "dmz",
                                     "hostgruppen": ["cue"]}, "lage": {"nachrichten": []}}


async def _lage(start, stunden):
    return {"startfilter": start, "nachrichten": [{"id": "graylog:m1", "text": "OOM"}]}


async def _schreiber(zuege):
    PROTOKOLL[:] = zuege
    return len(zuege)


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
    erg = asyncio.run(tr.analysiere({"start": "host:a", "anzahl": 7, "hosts": ["a"]}, object(), ki, bauer=_bauer, schreiber=_schreiber, lage_bauer=_lage))
    assert erg["befund"] == "ki:abc"
    assert [s["werkzeug"] for s in erg["schritte"]] == ["lw_wissen", "lw_facetten", "lw_befund", "lw_befund"]
    assert erg["schritte"][2]["fehler"].startswith("kein geprüfter Beleg")
    assert "nicht wörtlich" in ki.gesehen[3][-1]          # Ablehnung ging an die KI zurück


def test_ohne_befund_benannt(monkeypatch):
    ki = _FakeKI([[], []])
    erg = asyncio.run(tr.analysiere({"start": "host:a", "anzahl": 1, "hosts": ["a"]}, object(), ki, bauer=_bauer, schreiber=_schreiber, lage_bauer=_lage))
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
    f = tr.faelle(n)
    assert [(x["start"], x["anzahl"], x["hosts"]) for x in f] == [("muster:m1", 3, ["cue0174", "cue222"]),
                                                                  ("host:srv032", 1, ["srv032"])]


def test_faelle_dringend_dann_aelteste_dann_groesse():
    """Regression 07.11.: CRIT auf Prod (Einzelfall) verfiel hinter lauten großen Fällen."""
    n = ([{"host": "dmzdocker", "zeit": "2026-11-07T00:20:00Z", "hashtags": ["muster:laut"]}] * 26
         + [{"host": "mssql014", "zeit": "2026-11-07T00:25:00Z", "hashtags": ["signal:dringend", "zustand:crit"]}]
         + [{"host": "alt", "zeit": "2026-11-06T22:40:00Z", "hashtags": ["muster:alt"]}])
    assert [x["start"] for x in tr.faelle(n)] == ["host:mssql014", "muster:alt", "muster:laut"]


def test_runden_parallel_nur_mit_disjunkten_hosts():
    f = lambda start, *h: {"start": start, "hosts": list(h)}
    faelle = [f("muster:a", "cue0148", "cue0112"), f("host:cue0148", "cue0148"), f("host:mssql05", "mssql05"),
              f("host:mon03", "mon03"), f("muster:b", "cue0112")]
    r = tr.runden(faelle, 2)
    assert [[x["start"] for x in runde] for runde in r] == [
        ["muster:a", "host:mssql05"], ["host:cue0148", "host:mon03"], ["muster:b"]]
    assert sum(len(x) for x in r) == len(faelle)                 # nichts fällt still heraus



def test_protokoll_und_host_objekt_im_prompt(monkeypatch):
    _rufe(monkeypatch, {"lw_suche": {"gesamt": 3}, "lw_befund": {"id": "ki:abc", "belege": 1, "verworfen": []}})
    ki = _FakeKI([[_aufruf("lw_suche", filter="host:a thema:oom")], [_aufruf("lw_befund", titel="T", befund="B")]])
    erg = asyncio.run(tr.analysiere({"start": "muster:m1", "anzahl": 2, "hosts": ["a"]}, object(), ki,
                                    bauer=_bauer, schreiber=_schreiber, lage_bauer=_lage))
    assert erg["befund"] == "ki:abc" and erg["sitzung"]
    assert [z["rolle"] for z in PROTOKOLL] == ["worker", "ki", "werkzeug", "ki", "befund"]
    assert '"netz": "dmz"' in PROTOKOLL[0]["text"]                      # Host-Objekt ist der Prompt
    assert '"startfilter": "muster:m1"' in PROTOKOLL[0]["text"]          # Lage des FALLS, nicht je Host
    assert "NICHT erneut mit dem Startfilter" in PROTOKOLL[0]["text"]
    tags = set(PROTOKOLL[0]["hashtags"])
    assert {"host:a", "muster:m1", "ort:MUE-0", "kritikalitaet:prod", "netz:dmz", "hg:cue", "befund:abc"} <= tags
    assert PROTOKOLL[2]["werkzeug"] == "lw_suche" and PROTOKOLL[4]["befund"] == "ki:abc"
    assert len({z["sitzung"] for z in PROTOKOLL}) == 1 and [z["zug"] for z in PROTOKOLL] == [0, 1, 2, 3, 4]


def test_protokoll_auch_bei_fehler(monkeypatch):
    class _Kaputt:
        async def __call__(self, *a):
            raise TimeoutError("ReadTimeout")
    erg = asyncio.run(tr.analysiere({"start": "host:a", "anzahl": 1, "hosts": ["a"]}, object(), _Kaputt(),
                                    bauer=_bauer, schreiber=_schreiber, lage_bauer=_lage))
    assert erg["fehler"].startswith("TimeoutError") and [z["rolle"] for z in PROTOKOLL] == ["worker"]


def test_trichter_bietet_lw_wissen_nicht_an():
    """Gedächtnis steht im Prompt — lw_wissen wäre ein zweiter Weg zum selben Ergebnis."""
    namen = [t["function"]["name"] for t in tr.wz.openai_tools(tr.TRICHTER_WERKZEUGE)]
    assert "lw_wissen" not in namen and "lw_befund" in namen and set(namen) <= set(tr.wz.REGISTRY)
