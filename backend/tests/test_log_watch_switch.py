"""Switches im Raster: eigener Lerner, Lernphase, Vokabular, Urteile nach Regelwerk."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.error_clusters.miner import Miner
from app.services.log_watch import switch as sw
from app.services.log_watch.definition import STANDARD
from app.services.log_watch.hosts import Hostverzeichnis
from app.services.log_watch.vokabular import themen_switch

START = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
NACH = START + timedelta(hours=25)
VERZ = Hostverzeichnis.aus_checkmk([{"hostname": "nsc04-q1", "metadata": {"criticality": "prod",
                                                                         "location": "MUE-0"}}])


@pytest.mark.parametrize("text,erwartet", [
    ("%LINK-3-UPDOWN: Interface Gi1/0/12, changed state to down", {"link-down"}),   # Cisco
    ("%LINK-3-UPDOWN: Interface Gi1/0/12, changed state to up", set()),
    ("Port 12 is now off-line", {"link-down"}),
    ("%PM-4-ERR_DISABLE: bpduguard error detected on Gi1/0/3, putting Gi1/0/3 in err-disable state", {"err-disable"}),
    ("STP: Topology change detected on port 24", {"stp"}),
    ("Loop protection: loop detected on port 7", {"schleife"}),
    ("Power supply 2 failed", {"hardware"}),
    ("Fan module 1 fault", {"hardware"}),
    ("PoE: port 5 power denied, overload", {"poe"}),
    # Juniper EX, live 06.11.
    ("fpc1 [EX-BCM PIC] ex_bcm_linkscan_handler: Link 12 DOWN", {"link-down"}),
    ("fpc1 [EX-BCM PIC] ex_bcm_linkscan_handler: Link 12 UP", set()),
    ("mib2d[1784]: SNMP_TRAP_LINK_DOWN: ifIndex 529, ifAdminStatus up(1), ifOperStatus down(2), ifName ge-0/0/3",
     {"link-down"}),
])


def test_switch_vokabular(text, erwartet):
    assert {t.id for t in themen_switch(text)} - {"timeout"} == erwartet


def test_geraetename_vorn_wird_entfernt_ein_muster_fuer_alle_switches():
    assert sw.ohne_geraetename("nsa429 fpc1 [EX-BCM PIC] Link 7 DOWN", "nsa429") == "fpc1 [EX-BCM PIC] Link 7 DOWN"
    assert sw.ohne_geraetename("nsa429.example.com: x", "nsa429.example.com") == "x"
    assert sw.ohne_geraetename("fpc1 Link 7 DOWN", "nsa429") == "fpc1 Link 7 DOWN"   # nichts zu tun
    m = Miner()
    keys = {m.add(sw.ohne_geraetename(f"{h} fpc1 [EX-BCM PIC] ex_bcm_linkscan_handler: Link 7 DOWN", h)).key
            for h in ("nsa340", "nsa386", "nsa429")}
    assert len(keys) == 1


def test_lernphase_kein_neu_danach_schon():
    assert not sw.ist_neu("k", set(), START, START + timedelta(hours=2))
    assert sw.ist_neu("k", set(), START, NACH)
    assert not sw.ist_neu("k", {"k"}, START, NACH)


def _zeilen():
    return [{"_id": "a", "source": "nsc04-q1", "level": 3, "timestamp": "t",
             "message": "Port 12 is now off-line"},
            {"_id": "b", "source": "nsc04-q1", "level": 3, "timestamp": "t",
             "message": "Port 13 is now off-line"},
            {"_id": "c", "source": "nsc04-q1", "level": 2, "timestamp": "t",
             "message": "Power supply 2 failed"}]


def _gruppen():
    m = Miner()

    def lerne(text, quelle):
        r = m.add(sw.ohne_geraetename(text, quelle))
        return (r.key, r.template) if r else None
    return sw.gruppen(_zeilen(), lerne)


def test_gruppen_je_muster_und_switch():
    g = _gruppen()
    port = next(x for x in g if "off-line" in x["template"])
    assert port["anzahl"] == 2 and port["belege"] == [("a", "t"), ("b", "t")] and port["level"] == 3


def test_urteile_lernphase_neu_routine_hardware():
    g = _gruppen()
    lern = {x.beispiel: x for x in sw.bewerte_switch(g, set(), START, START + timedelta(hours=1), VERZ, STANDARD)}
    port = lern["Port 12 is now off-line"]
    assert (port.urteil, port.kriterien) == ("nicht_relevant", ["N1"])        # Lernphase: nicht neu
    assert "Lernphase bis" in port.gruende[-1]
    psu = lern["Power supply 2 failed"]
    assert (psu.urteil, psu.regel, psu.relevanz) == ("relevant", "R5", 3)      # Schaden, Level 2 dringend
    assert {"quelle:switch", "switch:nsc04-q1", "zustand:crit", "ort:MUE-0"} <= set(psu.hashtags)

    spaeter = {x.beispiel: x for x in sw.bewerte_switch(g, set(), START, NACH, VERZ, STANDARD)}
    assert (spaeter["Port 12 is now off-line"].urteil, spaeter["Port 12 is now off-line"].regel) == ("relevant", "R2")
    bekannt = {x["key"] for x in g}
    alt = {x.beispiel: x for x in sw.bewerte_switch(g, bekannt, START, NACH, VERZ, STANDARD)}
    assert alt["Port 12 is now off-line"].kriterien == ["N1"]                 # bekannt, kein Signal: Routine


def test_up_und_down_im_selben_muster_werden_getrennt_beurteilt():
    m = Miner()

    def lerne(text, quelle):
        r = m.add(sw.ohne_geraetename(text, quelle))
        return (r.key, r.template) if r else None
    zeilen = [{"source": "nsa1", "level": 4, "message": f"nsa1 fpc0 [EX-BCM PIC] ex_bcm_linkscan_handler: Link {n} {z}"}
              for n, z in ((1, "UP"), (2, "DOWN"), (3, "UP"), (4, "DOWN"), (5, "DOWN"))]
    g = sw.gruppen(zeilen, lerne)
    assert len({x["key"] for x in g}) == 1                       # Drain: ein Muster
    je = {tuple(x["themen"]): x["anzahl"] for x in g}
    assert je == {(): 2, ("link-down",): 3}                     # Themen trennen UP und DOWN
