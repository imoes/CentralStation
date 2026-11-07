"""Jede Nachricht ist ein Objekt: feste Felder, Methoden mit Urheber und Grund, Vorrang Mensch > KI > Regel,
neu bewerten mit geltendem Vokabular und Regelwerk (Nutzer 07.11.)."""
from datetime import datetime, timezone

import pytest

from app.services.log_watch import nachricht as nm
from app.services.log_watch.definition import STANDARD

JETZT = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def test_feste_hashtags_und_grund_sind_pflicht():
    with pytest.raises(nm.NachrichtAbgelehnt, match="festes Feld"):
        nm.pruefe_hashtags(["host:x"], [])
    with pytest.raises(nm.NachrichtAbgelehnt, match="art:wert"):
        nm.pruefe_hashtags(["ohneart"], [])
    nm.pruefe_hashtags(["thema:oom"], ["signal:schaden"])
    with pytest.raises(nm.NachrichtAbgelehnt, match="Grund ist Pflicht"):
        nm._eintrag("mensch", "x", " ", JETZT)
    with pytest.raises(nm.NachrichtAbgelehnt, match="Urheber"):
        nm._eintrag("praktikant", "x", "weil", JETZT)
    assert nm._eintrag("ki", "verknüpft", "Beleg", JETZT)["zeit"] == JETZT.isoformat()


def test_fehltreffer_oom_wird_neu_bewertet():
    """Gemessen 07.11.: memory.events-Zähler war thema:oom + signal:schaden (R5). Neu bewertet: Thema weg,
    Signal weg (ein Thema war sein einziger Grund), Urteil nach dem Regelwerk."""
    doc = {"text": '[info]: {"key":"oom_kill","value":"0"}', "quelle": "graylog", "herkunft": "regel",
           "hashtags": ["quelle:graylog", "host:jump012", "muster:m1", "thema:oom", "signal:schaden"],
           "gruende": ["Thema „Speicher erschöpft“ (Schaden an Ressourcen)"], "urteil": "relevant",
           "relevanz": 2, "kriterien": ["R5"]}
    a = nm.neu_bewertet(doc, STANDARD.regeln, 7)
    assert "thema:oom" not in a["hashtags"] and "signal:schaden" not in a["hashtags"]
    assert a["gruende"] == [] and a["urteil"] == "nicht_relevant" and "R5" not in a["kriterien"]


def test_neu_bewerten_laesst_andere_gruende_und_hoehere_urheber_stehen():
    kernel = {"text": "kernel: segfault at 27", "quelle": "graylog", "herkunft": "regel",
              "hashtags": ["quelle:graylog", "thema:absturz", "signal:schaden"],
              "gruende": ["Thema „Prozess abgestürzt“ (Schaden an Ressourcen)"], "urteil": "relevant",
              "relevanz": 2, "kriterien": ["R5"]}
    assert nm.neu_bewertet(kernel, STANDARD.regeln, 7) is None          # unverändert richtig
    mensch = {"text": "harmlos", "quelle": "graylog", "herkunft": "mensch", "hashtags": ["quelle:graylog"],
              "gruende": [], "urteil": "relevant", "relevanz": 3, "kriterien": ["R1"]}
    assert nm.neu_bewertet(mensch, STANDARD.regeln, 7) is None          # Mensch schlägt Regel


def test_skripte_tragen_die_geschichte():
    for s in (nm.SKRIPT_VERKNUEPFE, nm.SKRIPT_MARKIERE, nm.SKRIPT_URTEILE):
        assert "aenderungen.add(params.e)" in s
    assert "params.rang[params.e.wer] < alt" in nm.SKRIPT_URTEILE   # Vorrang im Schreibweg selbst
