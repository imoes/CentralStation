"""Kalibrierung der Intuition (VI, 08.11.): R7 nur zwischen Symptomen; Flattern als ein Problem; Inventar als Test."""
from app.services.log_watch import raster
from app.services.log_watch.definition import NICHT_RELEVANT, RELEVANT, STANDARD
from app.services.log_watch.hosts import Hostverzeichnis
from app.services.log_watch.regelwerk import TEST, bewerte


def _t(quelle, host, tags, level=None, urteil=RELEVANT):
    return raster.Treffer(quelle, "x", host, 2, [], tags, 1, "", True, urteil=urteil, kriterien=["R2"],
                          felder={"level": level} if level is not None else {})


def test_r7_nur_zwischen_symptomtragenden_treffern():
    """Gemessen 08.11.: 619 von 1 232 R7 hatten als Partner nur Wazuh-Level-7-Inventar (533 netstat)."""
    app = _t("graylog", "cue0111", ["quelle:graylog", "signal:neu"])                 # neue App-Warnung, kein Symptom
    netstat = _t("wazuh", "cue0111", ["quelle:wazuh", "regel:533"], level=7)         # Inventar
    assert raster.quellenuebergreifend([app, netstat]) == set() and "R7" not in app.kriterien and app.relevanz == 2
    disk = _t("graylog", "mon03", ["quelle:graylog", "thema:disk-full", "signal:schaden"])
    crit = _t("checkmk", "mon03", ["quelle:checkmk", "zustand:crit"])
    assert raster.quellenuebergreifend([disk, crit]) == {"mon03"}
    assert "R7" in disk.kriterien and disk.relevanz == 3 and "R7" in crit.kriterien
    # Wazuh ab der dringenden Stufe zählt als Symptom; ein weiterer nicht-symptomtragender Treffer am Host bleibt 2
    w12 = _t("wazuh", "web01", ["quelle:wazuh", "regel:31533"], level=12)
    fw = _t("firewall", "web01", ["quelle:firewall", "signal:bedrohung"])
    laut = _t("graylog", "web01", ["quelle:graylog", "signal:neu"])
    assert raster.quellenuebergreifend([w12, fw, laut]) == {"web01"}
    assert w12.relevanz == 3 and fw.relevanz == 3 and laut.relevanz == 2 and "R7" not in laut.kriterien


def test_flattern_ist_ein_signal_und_testregel():
    p = {"host": "mon03.example.com", "service": "OMD mue performance", "severity": "critical",
         "output": "CRIT - x", "metadata": {"criticality": "prod"}}
    kanon = raster._host_tags(p["host"], Hostverzeichnis())[0]          # Schlüssel wie in der Historie
    t = raster.bewerte_checkmk({}, [p], Hostverzeichnis(), STANDARD,
                               wechsel={(kanon, "dienst:OMD mue performance"): 60}).treffer[0]
    assert "signal:flattern" in t.hashtags and any("60 Zustandswechsel" in g for g in t.gruende)
    assert "N1-FLAT" in t.test_treffer and t.urteil == RELEVANT            # Test: zählt nur, entscheidet nicht
    t2 = raster.bewerte_checkmk({}, [p], Hostverzeichnis(), STANDARD, wechsel={}).treffer[0]
    assert "signal:flattern" not in t2.hashtags


def test_wazuh_inventar_zaehlt_im_test_und_bleibt_r6():
    regeln = STANDARD.regeln
    assert next(r for r in regeln if r.id == "N1-INV").modus == TEST
    assert next(r for r in regeln if r.id == "N1-FLAT").modus == TEST
    e = bewerte(regeln, {"quelle:wazuh", "regel:533", "regelgruppe:ossec"}, {"level": 7})
    assert e.regel == "R6" and "N1-INV" in e.test_treffer
    e = bewerte(regeln, {"quelle:wazuh", "regel:533"}, {"level": 12})
    assert "N1-INV" not in e.test_treffer                                   # ab Level 10 kein Inventar
    assert [r.id for r in regeln].index("N1-INV") < [r.id for r in regeln].index("R6")
