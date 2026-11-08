"""Regelwerk der Log-Überwachung (Firewall-Semantik): Auswertung, Gleichwertigkeit mit dem
bisherigen Vorrang, Testmodus, Verdeckung, Prüfung neuer Regelsätze."""
import itertools

import pytest

from app.services.log_watch import regelwerk as rw
from app.services.log_watch.definition import STANDARD


def entscheide(r: set[str], n: set[str]) -> tuple[str, list[str]]:
    """PRÜF-ORAKEL: der mit dem Nutzer vereinbarte Vorrang in seiner ursprünglichen Form
    (bis 06.11. Produktcode). Das Regelwerk muss für jede Kombination dasselbe Urteil fällen."""
    if "N6" in n:
        return "nicht_relevant", ["N6"]
    if "N2" in n:
        return "nicht_relevant", ["N2"]
    if "N4" in n:
        return "nicht_relevant", ["N4"]
    filter_ = n & {"N3", "N5"}
    if filter_:
        if r & {"R5", "R6"}:
            return "relevant", sorted(r)
        return "nicht_relevant", sorted(filter_)
    if r:
        return "relevant", sorted(r)
    return "nicht_relevant", sorted(n & {"N1"}) or ["N0"]

REGELN = STANDARD.regeln

#: Kriterium → Merkmale, mit denen eine Quelle es ausdrückt
MERKMALE = {
    "N6": ({"signal:stumm"}, {}), "N2": ({"signal:behandelt"}, {}), "N4": ({"regel:5501"}, {}),
    "N3": ({"kritikalitaet:test"}, {}), "N5": ({"dienst:Check_MK Discovery"}, {}),
    "N1": ({"quelle:graylog"}, {}),
    "R1": ({"zustand:crit", "kritikalitaet:prod"}, {}), "R2": ({"signal:neu"}, {}),
    "R3": ({"signal:ausbreitung"}, {}), "R4": ({"signal:anstieg"}, {}),
    "R5": ({"signal:schaden"}, {}), "R6": ({"quelle:wazuh"}, {"level": 9}),
    "R8": ({"signal:frist"}, {}),
}
ALLE = sorted(MERKMALE)


def test_gleichwertig_mit_bisherigem_vorrang_fuer_alle_kombinationen():
    """Alle 2^13 Kombinationen: dasselbe Urteil; die entscheidende Regel ist eines der
    Kriterien, die der bisherige Vorrang nannte."""
    for n in range(len(ALLE) + 1):
        for kombi in itertools.combinations(ALLE, n):
            tags, felder = set(), {}
            for k in kombi:
                tags |= MERKMALE[k][0]
                felder.update(MERKMALE[k][1])
            r = {k for k in kombi if k.startswith("R")}
            nn = {k for k in kombi if k.startswith("N")}
            alt_urteil, alt_krit = entscheide(r, nn)
            e = rw.bewerte(REGELN, tags, felder)
            neu_urteil = "relevant" if e.aktion == rw.FORWARD else "nicht_relevant"
            assert neu_urteil == alt_urteil, (kombi, alt_krit, e)
            assert e.kriterien[0] in alt_krit, (kombi, alt_krit, e)


def test_default_policy_block_n0():
    e = rw.bewerte(REGELN, {"quelle:wazuh"}, {"level": 3})
    assert (e.aktion, e.regel, e.kriterien) == ("block", "default", ["N0"])


def test_erste_passende_entscheidet_weitere_gleiche_aktion_zitiert_verdraengte_gezaehlt():
    e = rw.bewerte(REGELN, {"signal:schaden", "zustand:crit", "kritikalitaet:prod", "quelle:graylog"})
    assert (e.aktion, e.regel) == ("forward", "R5")
    assert e.kriterien == ["R5", "R1"]                       # entscheidend zuerst
    assert e.verdraengt == ["R1", "N1"]                      # passten, entschieden aber nicht


def test_testmodus_zaehlt_nur():
    probe = rw.Regel("K1", "N4", "FTP-Rauschen (Vorschlag)", rw.BLOCK,
                     {"alle": [{"hat": "regel:11307"}, {"hat": "host:ftpp01-v.zo-intern.de"}]},
                     modus=rw.TEST, herkunft="ki")
    e = rw.bewerte([probe] + REGELN, {"quelle:wazuh", "regel:11307", "host:ftpp01-v.zo-intern.de"},
                   {"level": 10})
    assert (e.aktion, e.regel, e.test_treffer) == ("forward", "R6", ["K1"])


@pytest.mark.parametrize("bed,tags,felder,erwartet", [
    ({"hat": "a"}, {"a"}, {}, True),
    ({"eines_von": ["a", "b"]}, {"b"}, {}, True),
    ({"eines_von": []}, {"b"}, {}, False),
    ({"praefix": "dienst:Check_MK"}, {"dienst:Check_MK Discovery"}, {}, True),
    ({"praefix": ["x:", "y:"]}, {"y:1"}, {}, True),
    ({"praefix": []}, {"y:1"}, {}, False),
    ({"nicht": {"hat": "a"}}, {"a"}, {}, False),
    ({"feld": "level", "ab": 7}, set(), {"level": 7}, True),
    ({"feld": "level", "ab": 7, "bis": 11}, set(), {"level": 12}, False),
    ({"feld": "level", "ab": 7}, set(), {}, False),          # Feld fehlt: passt nicht
    ({"unbekannt": 1}, {"a"}, {}, False),                    # unbekannt: passt nie
])
def test_bedingungen(bed, tags, felder, erwartet):
    assert rw.passt(bed, tags, felder) is erwartet


def test_regelsatz_pruefen_und_rundreise():
    daten = rw.als_daten(REGELN)
    ids = {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "N0", "N1", "N2", "N3", "N4", "N5", "N6"}
    assert rw.pruefe(daten, ids, STANDARD.objekte) == []
    assert rw.aus_daten(daten, STANDARD.objekte) == REGELN
    assert rw.pruefe([{**daten[0], "filter": "@Gibtsnicht"}], ids, STANDARD.objekte) == [
        "N6: Filter '@Gibtsnicht': Objekt @Gibtsnicht gibt es nicht"]
    kaputt = daten + [{"id": "N6", "kriterium": "X9", "aktion": "drop", "modus": "egal",
                       "bedingung": {"feld": "level"}}]
    f = rw.pruefe(kaputt, ids)
    assert "Regel-IDs doppelt" in f and any("forward, block oder incident" in x for x in f)
    assert any("X9" in x for x in f) and any("wohlgeformt" in x for x in f)


def test_offline_ist_nicht_produktion_v6():
    """Definition v6 (Nutzer 06.11.): offline zählt wie test/dev als Nicht-Produktion — R5/R6 bleiben vorne."""
    from app.services.log_watch.definition import STANDARD
    from app.services.log_watch.regelwerk import bewerte
    e = bewerte(STANDARD.regeln, {"kritikalitaet:offline", "signal:neu"}, {})
    assert e.regel == "N3"
    e = bewerte(STANDARD.regeln, {"kritikalitaet:offline", "signal:schaden"}, {})
    assert e.regel == "R5"
    e = bewerte(STANDARD.regeln, {"kritikalitaet:install", "signal:neu"}, {})
    assert e.regel == "R2"                                   # install: kein Kriterium, benannt offen


def test_incident_regeln_zaehlen_nicht_im_urteil_und_werden_geprueft():
    """Incident-Regeln (Aktion incident) stehen in derselben Regeltabelle, entscheiden aber nie über
    eine einzelne Nachricht; der Prüfer verlangt Gruppe, Fenster, Mindestwerte und Art."""
    inc = [r for r in REGELN if r.aktion == rw.INCIDENT]
    assert [r.id for r in inc] == ["I-AUSBRUCH", "I-FLOTTE", "I-KRITISCH", "I-HOST"]
    assert all(r.modus == rw.TEST for r in inc)          # erst Schattenbetrieb
    e = rw.bewerte(inc, {"relevanz:3", "quelle:graylog", "signal:dringend"}, {"relevanz": 3})
    assert e.regel == "default" and e.test_treffer == []
    ids = {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "N0", "N1", "N2", "N3", "N4", "N5", "N6"}
    d = rw.als_daten(inc)
    assert rw.pruefe(d, ids, STANDARD.objekte) == []
    kaputt = [{**d[0], "id": "X", "gruppe": "egal", "fenster_min": 0, "mindestens": {"tage": 1}, "incident_art": "?"}]
    f = rw.pruefe(kaputt, ids, STANDARD.objekte)
    assert len(f) == 4 and any("gruppe" in x for x in f) and any("incident_art" in x for x in f)


def test_ansible_ist_selbst_erzeugt_v8():
    """Definition v8 (Nutzer 07.11.): Ansible-Modulaufrufe sind N5 — auch dringend und sich ausbreitend auf prod;
    Schaden (R5) bleibt vorne."""
    from app.services.log_watch.definition import STANDARD
    from app.services.log_watch.raster import apps_aus_text
    from app.services.log_watch.regelwerk import bewerte
    assert apps_aus_text("python3[<NUM>]: ansible-apt Invoked with name=filebeat=<NUM>.<NUM>") == ["ansible"]
    assert apps_aus_text("<NUM> <TS> <*> python3 <NUM> ansible-ansible.legacy.stat Invoked with <*>") == ["ansible"]
    assert apps_aus_text("ansible-apt: Invoked with name=filebeat") == ["ansible"]
    assert apps_aus_text("sshd[1]: pam_env(sshd:session): deprecated", "ansible-runner sshd-session[2]: x") == []
    for t in ("sudo - ansible : TTY=pts/<NUM> ; PWD=/systems/ansible ; USER=root ; COMMAND=/bin/sh -c",
              "sudo: ansible : (command continued) print(json.dumps(out))",
              "<TS> p=<NUM> u=root n=ansible | <*> : ok=<NUM> changed=<NUM> failed=<NUM>",
              "<TS> p=<NUM> <*> n=ansible | [WARNING]: Could not match supplied host pattern",
              "ansible-find: [WARNING] Skipped <*> path due to this access issue",
              "sshd <NUM> Disconnected from user ansible <IP> port <NUM>"):
        assert apps_aus_text(t) == ["ansible"], t
    assert apps_aus_text("ansible-runner certmonger[<NUM>]: Error binding to <*> Local error.") == []
    tags = {"quelle:graylog", "app:ansible", "kritikalitaet:prod", "signal:ausbreitung", "signal:dringend"}
    assert bewerte(STANDARD.regeln, tags, {}).regel == "N5"
    assert bewerte(STANDARD.regeln, tags | {"signal:schaden"}, {}).regel == "R5"
    assert bewerte(STANDARD.regeln, tags - {"app:ansible"}, {}).regel != "N5"


def test_sshd_sitzungszeilen_sind_rauschen():
    """Nutzer 07.11.: pam_env/syslogin_perform_logout bei jedem SSH-Login → rauschen:sshd-sitzung (N4 über @Rauschen)."""
    from app.services.log_watch.raster import tags_aus_text
    assert tags_aus_text("ns03 sshd[<NUM>]: pam_env(sshd:session): deprecated reading of user environment enabled") == ["rauschen:sshd-sitzung"]
    assert tags_aus_text("docker0188 sshd-session[<NUM>]: syslogin_perform_logout: logout() returned an error") == ["rauschen:sshd-sitzung"]
    assert tags_aus_text("sshd[1]: Failed password for root from 1.2.3.4") == []
