"""Auffälligkeiten (IsolationForest) und Lernstand: Merkmale, Schwelle, Grund, Wirkung im Raster über R4-AUF,
Vergleich mit Regeln/KI, Trend je Trainingslauf, Wirkung je Zeitraum."""
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.log_watch import auffaellig as af
from app.services.log_watch import lernstand as ls
from app.services.log_watch import raster
from app.services.log_watch import relevanz_modell as rm
from app.services.log_watch.definition import PARAMETER, STANDARD, STANDARD_OBJEKTE, Definition
from app.services.log_watch.regelwerk import AKTIV, STANDARD_FILTER, standard_regeln

B = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _leer():
    af.setze(None)
    rm.setze(None)
    yield
    af.setze(None)
    rm.setze(None)


def _stand():
    """Realistische Streuung in ALLEN Merkmalen — an einem im Training konstanten Merkmal kann der Wald nicht teilen."""
    rnd = random.Random(3)
    x = []
    for _ in range(5000):
        mittel = rnd.uniform(1, 50)
        x.append(af.merkmal_zeile(max(1, int(rnd.gauss(mittel, mittel * 0.3))), mittel, rnd.randint(1, 4),
                                  rnd.uniform(24, 5000), rnd.uniform(0.2, 1.0)))
    m = af.trainiere(x)
    # m1: neu (1 h alt), selten (Präsenz 0,1 %) — m2: üblich
    return {"modell": m, "normal": af.normal(x), "stats": {"m1": (10.0, 0.001), "m2": (10.0, 0.8)},
            "erst": {"m1": B - timedelta(hours=1), "m2": B - timedelta(days=20)}, "schwelle": af.schwelle_aus(m, x)}


def test_reine_mengenspitze_eines_bekannten_musters_ist_keine_auffaelligkeit():
    """Gemessen 12.11.: IsolationForest sättigt jenseits des Trainingsbereichs — eine 500-fache Menge eines bekannten,
    häufigen Musters liegt unter der Schwelle. Das fangen die Statistikregeln (Anstieg/Ausbreitung); benannt."""
    st = _stand()
    z = [{"key": "a", "b": 0, "x": af.merkmal_zeile(5000, 10.0, 30, 480.0, 0.8)}]
    af.bewerte(st["modell"], st["normal"], z, st["schwelle"])
    assert not z[0]["ausreisser"]


def test_merkmale_und_grund():
    x = af.merkmal_zeile(1000, 10.0, 40, 0.5, 0.01)
    assert len(x) == len(af.MERKMALE) and x[1] > 4                  # weit über dem eigenen Normal
    g = af.grund(x, [(2.0, 0.5), (0.0, 0.3), (0.7, 0.3), (6.0, 0.5), (0.8, 0.1)])
    assert g and all("σ" in z for z in g)


def test_schwelle_quantil_und_ausreisser():
    st = _stand()
    zeilen = [{"key": "a", "b": 0, "x": af.merkmal_zeile(12, 10.0, 2, 500.0, 0.8)},
              {"key": "b", "b": 0, "x": af.merkmal_zeile(5000, 10.0, 60, 0.2, 0.001)}]
    af.bewerte(st["modell"], st["normal"], zeilen, st["schwelle"])
    assert zeilen[1]["ausreisser"] and not zeilen[0]["ausreisser"] and zeilen[1]["auffaellig"] > zeilen[0]["auffaellig"]
    assert zeilen[1]["grund"]


def _mit_aktiven_modellregeln():
    regeln = [replace(r, modus=AKTIV) if r.id in ("N1-MOD", "R4-AUF") else r
              for r in standard_regeln(dict(STANDARD_OBJEKTE))]
    return Definition(17, [], dict(PARAMETER), regeln=regeln)


def test_auffaelliges_fenster_wird_weitergeleitet_normales_nicht():
    af.setze(_stand())
    d = _mit_aktiven_modellregeln()
    # m1: 5000 Zeilen auf 30 Hosts (weit über Normal 10); m2: üblich. Beide ohne Signal → sonst Default N0
    tr = [raster._urteil("graylog", "muster:m1", f"h{i}", True, ["quelle:graylog", "muster:m1"], {}, [], 5000 // 30,
                         "x", d) for i in range(30)]
    tr.append(raster._urteil("graylog", "muster:m2", "h0", True, ["quelle:graylog", "muster:m2"], {}, [], 11, "x", d))
    assert all(t.urteil == "nicht_relevant" for t in tr)
    raster.mit_modell(tr, d, B)
    m1, m2 = tr[0], tr[-1]
    assert af.SIGNAL in m1.hashtags and m1.urteil == "relevant" and m1.regel == "R4-AUF"
    assert raster.SIG_AUFF_ENTSCHIED in m1.hashtags and any("Auffällig (IsolationForest" in g for g in m1.gruende)
    assert af.SIGNAL not in m2.hashtags and m2.urteil == "nicht_relevant"
    pruefen = [t for t in tr if raster.SIG_AUFF_PRUEFEN in t.hashtags]
    assert 0 < len(pruefen) < len(tr)                            # nur eine Stichprobe geht zur Messung an die KI
    z = raster.zaehle(tr, raster.Kontingent(), B)
    assert z["auffaellig"]["entschied"] == z["auffaellig"]["nachrichten"] > 0


def test_auffaellig_nie_gegen_nicht_produktion_und_testregel_zaehlt_nur():
    af.setze(_stand())
    ids = [x[0] for x in STANDARD_FILTER]
    assert ids.index("R4-AUF") > max(ids.index(r) for r in ("N3", "N5", "N1-MOD")) and ids.index("R4-AUF") < ids.index("N1")
    tr = [raster._urteil("graylog", "muster:m1", f"h{i}", True, ["quelle:graylog", "muster:m1"], {}, [], 200, "x",
                         STANDARD) for i in range(30)]
    raster.mit_modell(tr, STANDARD, B)                               # Standard: R4-AUF im Testmodus
    assert tr[0].urteil == "nicht_relevant" and "R4-AUF" in tr[0].test_treffer


def test_vergleich_mit_regeln_und_ki():
    zeilen = [{"key": "a", "b": 1, "ausreisser": True, "auffaellig": 0.8},
              {"key": "b", "b": 1, "ausreisser": True, "auffaellig": 0.7},
              {"key": "c", "b": 1, "ausreisser": False, "auffaellig": 0.3}]
    v = af.vergleich(zeilen, {("a", 1), ("c", 1)}, {("a", 1): 1, ("c", 1): 0})
    assert v["ausreisser"] == 2 and v["regeln_uebersehen"] == 1 and v["regeln_leiteten_weiter_bei_ausreissern"] == 0.5
    assert v["auc_gegen_ki_relevant"] == 1.0


def test_trend_je_trainingslauf():
    assert ls.trend([None, 0.7, 0.72, 0.75])["steigung_je_lauf"] > 0
    assert ls.trend([0.8, 0.8])["differenz"] == 0.0 and "steigung_je_lauf" not in ls.trend([0.8, 0.8])
    assert ls.trend([None]) == {"n": 0}


def test_wirkung_je_zeitraum_aus_raster_lauf():
    t0 = datetime(2026, 11, 12, tzinfo=timezone.utc)
    zeilen = [("graylog", t0, {"nachrichten": 100, "je_urteil": {"relevant": 10},
                               "modell_rauschen": {"nachrichten": 5, "entschied": 4, "erkundung": 1},
                               "auffaellig": {"nachrichten": 2, "entschied": 2}}),
              ("ki", t0, {"ok": True}), ("ki", t0 + timedelta(hours=3), {}),
              ("wazuh", t0, {"fehler": "x"})]
    s = ls.summe_fenster(zeilen, t0, t0 + timedelta(hours=2))
    assert (s["nachrichten"], s["relevant"], s["modell_geblockt"], s["modell_erkundung"], s["auffaellig_weitergeleitet"],
            s["ki_analysen"], s["fenster"]) == (100, 10, 4, 1, 2, 1, 1)
    assert s["je_stunde"]["nachrichten"] == 50.0
