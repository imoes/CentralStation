"""Regel-Editor: Treffer je Regel aus den Rasterläufen, Vorschau eines Entwurfs."""
from datetime import datetime, timezone

from app.services.log_watch import regel_editor as re_
from app.services.log_watch.definition import STANDARD
from app.services.log_watch.regelwerk import als_daten, aus_daten

T1 = datetime(2026, 11, 7, 14, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 7, 15, tzinfo=timezone.utc)


def test_treffer_je_regel_summiert_quellen_und_merkt_letzten():
    zeilen = [("graylog", T1, {"je_regel": {"R5": 2, "N1": 10}, "regel_verdraengt": {"R1": 2}, "regel_test": {"X": 1}}),
              ("wazuh", T2, {"je_regel": {"R5": 1}}),
              ("graylog", T2, {"fehler": "x", "je_regel": {"R5": 99}}),            # fehlerhafter Lauf zählt nicht
              ("uebergreifend", T2, {"je_regel": {"R5": 50}}),                     # keine Urteile je Nachricht
              ("incident-regeln", T2, {"je_regel": {"I-HOST": 3, "I-FLOTTE": 0}})]
    t = re_.treffer_je_regel(zeilen)
    assert t["R5"] == {"treffer": 3, "verdraengt": 0, "test": 0, "letzter": T2}
    assert t["R1"]["verdraengt"] == 2 and t["R1"]["letzter"] is None
    assert t["X"]["test"] == 1 and t["I-HOST"]["treffer"] == 3 and "I-FLOTTE" not in t


def test_vorschau_zeigt_geaenderte_urteile_nur_fuer_regel_urteile():
    alt = STANDARD.regeln
    daten = als_daten(alt)
    # Entwurf: eine neue Block-Regel vorn, die alles mit thema:oom verwirft
    neu = aus_daten([{"id": "A1", "kriterium": "N6", "name": "OOM stumm", "aktion": "block", "bedingung": {},
                      "filter": "thema:oom", "stufe_block": 0}] + daten, STANDARD.objekte)
    docs = [{"_id": "a", "hashtags": ["thema:oom", "signal:schaden", "quelle:graylog"], "herkunft": "regel"},
            {"_id": "b", "hashtags": ["thema:oom", "signal:schaden"], "herkunft": "ki"},      # KI hat Vorrang
            {"_id": "c", "hashtags": ["thema:dns", "signal:neu"], "herkunft": "regel"}]
    v = re_.vorschau(alt, neu, docs)
    assert v["geprueft"] == 2 and v["geaendert"] == 1
    assert v["beispiele"][0]["id"] == "a" and v["beispiele"][0]["nachher"] == {"aktion": "block", "regel": "A1"}
    assert list(v["je_wechsel"]) == ["R5 → A1"]
