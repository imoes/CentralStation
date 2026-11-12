"""Relevanz-Modell: Labels unabhängig von den Regeln, Merkmale, Kennzahlen, Modus mit Grund, Audit, Wirkung im Raster."""
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.log_watch import raster
from app.services.log_watch import relevanz_modell as rm
from app.services.log_watch.definition import KRITERIEN, PARAMETER, STANDARD, STANDARD_OBJEKTE, Definition
from app.services.log_watch.regelwerk import AKTIV, STANDARD_FILTER, standard_regeln

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ohne_modell():
    rm.setze(None)
    yield
    rm.setze(None)


# ── Labels und Merkmale ─────────────────────────────────────────────────────────

def test_merkmale_ohne_label_und_nachtraegliches():
    m = rm.merkmale("graylog", ["host:a", "befund:ki:1", "signal:modell-rauschen", "incident:x", "muster:m",
                                "ohne-doppelpunkt"], level="3", kriterien=["R2", "R7"], stunde=5)
    assert m == sorted(["quelle:graylog", "host:a", "muster:m", "level:3", "kriterium:R2", "stunde:05"])


def test_label_mensch_vor_ki_und_regel_nie():
    assert rm.label({"herkunft": "mensch", "relevanz": 1, "ai_relevanz": 3}) == (1, "gold")
    assert rm.label({"herkunft": "mensch", "relevanz": 2}) == (0, "gold")
    assert rm.label({"herkunft": "regel", "ai_relevanz": 1}) == (1, "silber")
    assert rm.label({"herkunft": "regel", "ai_relevanz": 3}) == (0, "silber")
    # nur das Urteil der Regeln → kein Label (sonst lernte das Modell die Regeln nach)
    assert rm.label({"herkunft": "regel", "relevanz": 3, "urteil": "relevant"}) is None


def test_beobachtung_ohne_ki_befunde_und_ohne_menschen_kriterien():
    assert rm.beobachtung("1", {"quelle": "ki", "ai_relevanz": 3}) is None
    b = rm.beobachtung("2", {"quelle": "graylog", "herkunft": "mensch", "relevanz": 0, "kriterien": ["N4"],
                             "hashtags": ["muster:m"], "zeit": "2026-11-01T03:00:00+00:00"})
    assert b.y == 1 and b.herkunft == "gold"
    assert not any(x.startswith("kriterium:") for x in b.merkmale)      # Kriterien des Menschen wären ein Leck
    b = rm.beobachtung("3", {"quelle": "graylog", "ai_relevanz": 2, "kriterien": ["R2"], "hashtags": [],
                             "zeit": "2026-11-01T03:00:00Z"})
    assert "kriterium:R2" in b.merkmale and "stunde:03" in b.merkmale and b.y == 0


# ── Kennzahlen ──────────────────────────────────────────────────────────────────

def test_auc():
    assert rm.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert rm.auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert rm.auc([0.5, 0.5], [0, 1]) == 0.5
    assert rm.auc([0.1, 0.2], [0, 0]) is None


def test_schwelle_laesst_hoechstens_ein_prozent_relevante_darueber():
    p = [i / 1000 for i in range(1000)]
    s = rm.schwelle_fuer(p)
    assert sum(1 for x in p if x >= s) / len(p) <= 0.01


def _daten(n: int, gold_anteil: float = 0.0, seed: int = 1) -> list[rm.Beobachtung]:
    """muster:laerm ist meist Rauschen, muster:ernst nie; Hosts zufällig — ein lernbares, ehrliches Problem."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        laerm = rnd.random() < 0.3
        y = 1 if (laerm and rnd.random() < 0.95) else 0
        herkunft = "gold" if rnd.random() < gold_anteil else "silber"
        mk = rm.merkmale("graylog", [f"muster:{'laerm' if laerm else 'ernst'}", f"app:a{rnd.randint(0, 3)}"],
                         kriterien=["R2"])
        out.append(rm.Beobachtung(str(i), (T0 + timedelta(minutes=i)).isoformat(), y, herkunft, mk,
                                  "muster:laerm" if laerm else "muster:ernst", "relevant", "R2", "x"))
    return out


def test_zu_wenig_daten_mit_grund():
    m = rm.trainiere(_daten(40))
    assert m["modus"] == "zu_wenig_daten" and "nötig je 50" in m["grund"]


def test_nur_silber_bleibt_test_mit_grund_und_lernt_das_muster():
    m = rm.trainiere(_daten(3000))
    assert m["modus"] == "test" and "Gold" in m["grund"]
    assert m["gewichte"]["muster:laerm"] > 0 > m["gewichte"]["muster:ernst"]
    assert m["kennzahlen"]["auc"] > 0.9
    w = m["widersprueche"]
    assert w["regel_relevant_modell_rauschen"] > 0 and w["je_kriterium"].get("R2")
    assert w["schluessel"][0]["schluessel"] == "muster:laerm"


def test_bereit_nur_mit_gold_kennzahlen_und_ohne_audit():
    labels = {"gesamt": {"rauschen": 900, "relevant": 2000}, "gold": {"rauschen": 60, "relevant": 70}}
    gut = {"auc_gold": 0.95, "recall_relevant_gold": 0.995}
    assert rm.modus(labels, gut, [])[0] == "bereit"
    assert rm.modus(labels, {**gut, "auc_gold": 0.6}, [])[0] == "test"
    assert rm.modus(labels, {**gut, "recall_relevant_gold": 0.9}, [])[0] == "test"
    m, grund = rm.modus(labels, gut, [{"klasse": "stunde", "merkmal": "stunde:13"}])
    assert m == "test" and "stunde:13" in grund
    assert rm.modus({**labels, "gold": {"rauschen": 10, "relevant": 70}}, gut, [])[0] == "test"


def test_audit_host_stunde_dominant():
    g = {"host:a": 3.0, "stunde:13": 2.0, "muster:x": 1.0}
    pos = [["muster:x", "host:a"]] * 30
    klassen = {b["klasse"] for b in rm.audit(g, [], pos)}
    assert klassen == {"host", "stunde", "dominant"}
    assert rm.audit({"muster:x": 1.0}, [], [["muster:x"]] * 5) == []      # zu wenige für „dominant“


def test_audit_klassen_erschoepfend():
    assert set(rm.AUDIT_KLASSEN) == {"dominant", "host", "stunde", "widerspricht"}
    assert rm.MODI == ("zu_wenig_daten", "test", "bereit")


# ── Wirkung im Raster: ein Signal, entscheiden tut das Regelwerk ─────────────────

MODELL = {"modus": "test", "grund": "Test", "gewichte": {"muster:laerm": 5.0}, "bias": -2.0, "schwelle": 0.5}
TAGS = ["quelle:graylog", "host:h", "signal:neu", "muster:laerm"]


def _urteil(d, tags=TAGS):
    return raster._urteil("graylog", "muster:laerm", "h", True, list(tags), {}, ["neu"], 3, "x", d)


def _mit_aktiver_n1mod():
    regeln = [replace(r, modus=AKTIV) if r.id == "N1-MOD" else r for r in standard_regeln(dict(STANDARD_OBJEKTE))]
    return Definition(16, [], dict(PARAMETER), regeln=regeln)


def test_ohne_modell_kein_signal():
    t = _urteil(STANDARD)
    assert rm.SIGNAL not in t.hashtags and t.regel == "R2"


def test_modell_setzt_signal_mit_tragenden_gewichten_testregel_zaehlt_nur():
    rm.setze(MODELL)
    t = _urteil(STANDARD)
    assert rm.SIGNAL in t.hashtags and t.regel == "R2" and t.urteil == "relevant"
    assert "N1-MOD" in t.test_treffer
    assert any("muster:laerm (+5.00)" in g for g in t.gruende)


def test_aktive_regel_wirkt_nicht_solange_modell_nicht_bereit():
    rm.setze(MODELL)
    t = _urteil(_mit_aktiver_n1mod())
    assert t.regel == "R2" and t.urteil == "relevant"
    assert any("N1-MOD übergangen: Modell nicht freigegeben" in g for g in t.gruende)
    assert "N1-MOD" in t.test_treffer


def test_aktive_regel_wirkt_bei_bereit_aber_nie_gegen_schaden():
    rm.setze({**MODELL, "modus": "bereit"})
    t = _urteil(_mit_aktiver_n1mod())
    assert t.regel == "N1-MOD" and t.urteil == "nicht_relevant" and t.kriterien[0] == "N1"
    t = _urteil(_mit_aktiver_n1mod(), TAGS + ["signal:schaden"])
    assert t.regel == "R5" and t.urteil == "relevant"


def test_n1mod_steht_nach_schaden_sicherheit_r1_und_vor_r2():
    ids = [x[0] for x in STANDARD_FILTER]
    i = ids.index("N1-MOD")
    assert all(ids.index(r) < i for r in ("R5", "R6", "R6-FW", "R1"))
    assert all(ids.index(r) > i for r in ("R2", "R3", "R4"))
    assert next(r for r in STANDARD.regeln if r.id == "N1-MOD").modus == "test"


def test_zaehle_weist_modell_rauschen_je_urteil_aus():
    rm.setze(MODELL)
    t = _urteil(STANDARD)
    z = raster.zaehle([t], raster.Kontingent(), datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert z["modell_rauschen"] == {"nachrichten": 3, "je_urteil": {"relevant": 3}, "je_kriterium": {"R2": 3}}


def test_kaputtes_modell_haelt_raster_nicht_an():
    rm.setze({"modus": "test", "gewichte": {"muster:laerm": "kaputt"}, "bias": 0, "schwelle": 0.5})
    assert _urteil(STANDARD).regel == "R2"


def test_kriterien_der_definition_unveraendert():
    assert {k.id for k in KRITERIEN} >= {"N1", "R2"}
