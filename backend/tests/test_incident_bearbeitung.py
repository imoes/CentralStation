"""Bearbeitet = Ruhe, Wiederauftreten statt neuer Incident (Nutzer 09.11.2026, Mail-Flut)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.error_clusters import bearbeitung as bb

J = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


def test_zustand_erschoepfend_ticket_vor_uebernahme_freigabe_hebt_auf():
    assert bb.zustand([])["zustand"] == "unbearbeitet"
    ue = {"type": "bearbeitung", "art": "übernommen", "von": "Thomas", "zeit": J.isoformat()}
    assert bb.zustand([ue])["zustand"] == "übernommen" and bb.zustand([ue])["von"] == "Thomas"
    frei = {"type": "bearbeitung", "art": "freigegeben", "von": "Thomas", "zeit": J.isoformat()}
    assert bb.zustand([ue, frei])["zustand"] == "unbearbeitet"
    t = {"type": "ticket", "key": "IMIT-1", "by": "Thomas", "at": J.isoformat()}
    z = bb.zustand([ue, frei, t])
    assert z == {"zustand": "ticket", "von": "Thomas", "zeit": J.isoformat(), "ticket": "IMIT-1", "grund": "Ticket IMIT-1"}
    assert {bb.zustand(x)["zustand"] for x in ([], [ue], [t])} == set(bb.ZUSTAENDE)


def test_bearbeitet_ist_ruhe_ausser_eskalation():
    ue = [{"type": "bearbeitung", "art": "übernommen", "von": "T", "zeit": J.isoformat()}]
    for anlass in ("wieder", "nachtrag"):
        ok, warum = bb.mail_erlaubt(anlass, ue, {}, J)
        assert not ok and "bearbeitet" in warum
    assert bb.mail_erlaubt("eskalation", ue, {}, J) == (True, "eskalation")
    assert bb.mail_erlaubt("neu", ue, {}, J)[0]
    with pytest.raises(ValueError):
        bb.mail_erlaubt("irgendwas", [], {}, J)


def test_unbearbeitet_wieder_erinnert_hoechstens_einmal_am_tag():
    assert bb.ERINNERUNG == timedelta(hours=24)
    ctx = {"zustellungen": [{"zeit": (J - timedelta(hours=20)).isoformat(), "mails": 1}]}
    ok, warum = bb.mail_erlaubt("wieder", [], ctx, J)
    assert not ok and "1200 min" in warum and "alle 24 h" in warum
    ctx = {"zustellungen": [{"zeit": (J - timedelta(hours=25)).isoformat(), "mails": 1},
                            {"zeit": (J - timedelta(minutes=10)).isoformat(), "mails": 0}]}   # 0 Mails zählt nicht
    assert bb.mail_erlaubt("wieder", [], ctx, J) == (True, "wieder aufgetreten, noch unbearbeitet")
    assert bb.mail_erlaubt("nachtrag", [], {}, J)[0]


def test_eskalation_neuer_host_oder_hoehere_schwere():
    alt = {"hosts": ["a.example.com"]}
    assert bb.eskalation(alt, ["A.example.com"], "medium", "medium") is None
    assert bb.eskalation(alt, ["a.example.com", "b.example.com"], "medium", "medium") == "neuer Host: b.example.com"
    assert bb.eskalation(alt, [], "medium", "high") == "Schwere gestiegen: medium → high"
    assert bb.eskalation(alt, [], "high", "medium") is None


def test_wiederauftreten_wird_vermerkt_und_gezaehlt():
    c = bb.wieder_vermerk({"regel": "I-HOST"}, J - timedelta(hours=30))
    c = bb.wieder_vermerk(c, J - timedelta(hours=2))
    c = bb.wieder_vermerk(c, J)
    assert len(c["wieder"]) == 3 and bb.anzahl_24h(c, J) == 3        # erstes Auftreten + 2 in 24 h
    assert bb.WIEDER_FENSTER == timedelta(hours=24)


def test_regelwerk_nutzt_24h_fenster():
    from app.services.log_watch import incident_regeln as ir
    assert ir.COOLDOWN == timedelta(hours=24)
