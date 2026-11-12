"""Lösungswege: Maßnahmen aus Logzeilen (festes Vokabular, nur ändernde Befehle), behoben mit Nachlauf, Basisrate
„ohne Maßnahme“, Modi mit Grund, strittig statt gelöscht, Zeitprüfung."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.log_watch import loesungen as lo

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("zeile,erwartet", [
    ("DOR-0-SDWAN sudo[5399]: unifi : PWD=/usr/lib/unifi ; USER=root ; COMMAND=/bin/systemctl start unifi-mongodb",
     "dienst-start:unifi-mongodb"),
    ("sudo: marvin : USER=root ; COMMAND=/usr/bin/systemctl restart nginx.service", "dienst-neustart:nginx"),
    ("sudo: x : COMMAND=/usr/sbin/service apache2 reload", "dienst-reload:apache2"),
    ("sudo: x : COMMAND=/usr/bin/docker restart cue-content-store-01", "docker-neustart:cue-content-store-01"),
    ("sudo: x : COMMAND=/usr/bin/docker compose -f /opt/x.yml up -d", "docker-compose:up"),
    ("sudo: x : COMMAND=/sbin/reboot", "host-neustart"),
    ("sudo: x : COMMAND=/usr/bin/apt-get -y install filebeat", "paket-install"),
    ("python3[1]: ansible-systemd Invoked with name=filebeat state=restarted daemon_reload=False",
     "dienst-neustart:filebeat"),
    ("python3[1]: ansible-ansible.legacy.systemd Invoked with daemon_reload=True name=filebeat.service state=started",
     "dienst-start:filebeat"),
    ("ansible-reboot Invoked with reboot_timeout=600", "host-neustart"),
    # lesend oder keine Änderung: nie eine Maßnahme
    ("sudo: marvin : USER=root ; COMMAND=/usr/bin/docker logs -t --since 1m cue-content-store-01", None),
    ("sudo: x : COMMAND=/usr/bin/systemctl status nginx", None),
    ("ansible-ansible.builtin.wait_for Invoked with host=graylog port=5044 state=started", None),
    ("ubnt-idsips-daemon: Info: Service[UnixGrpcServer] grpc command server listen", None),
])
def test_klassifiziere(zeile, erwartet):
    assert lo.klassifiziere(zeile) == erwartet


def test_vokabular_erschoepfend():
    for z in ("COMMAND=/bin/systemctl stop x", "COMMAND=/usr/bin/docker stop y", "COMMAND=/usr/bin/apt upgrade",
              "COMMAND=/usr/bin/docker-compose restart"):
        assert lo.klassifiziere(z).split(":")[0] in lo.ARTEN


def test_host_aus_syslog_weiterleitung():
    t = "0 2026-11-07T14:22:29+01:00 mu-img-cueapp03 python3 2270982  ansible-systemd Invoked with name=filebeat"
    assert lo.host_der_zeile("10.0.32.13", t) == "mu-img-cueapp3"           # norm: ohne führende Null
    assert lo.host_der_zeile("docker0216.example.com", "ohne Kennung") == "docker216"


def _ep(i, host="h", schl="muster:a", regel="I-AUSBRUCH", beginn_h=0, dauer_h=1, massnahme=None):
    e = lo.Episode(str(i), host, schl, regel, T0 + timedelta(hours=beginn_h),
                   T0 + timedelta(hours=beginn_h + dauer_h) if dauer_h is not None else None)
    if massnahme:
        e.massnahmen[massnahme] = ["zeile"]
    return e


def test_behoben_nur_ohne_wiederkehr_im_nachlauf_und_offen_benannt():
    a = _ep(1, beginn_h=0)
    b = _ep(2, beginn_h=10)                    # derselbe Schlüssel am selben Host 9 h nach dem Ende von a
    c = _ep(3, beginn_h=100)
    d = _ep(4, beginn_h=0, dauer_h=None)       # noch nicht zu
    jung = _ep(5, schl="muster:b", beginn_h=200)
    lo.setze_behoben([a, b, c, d, jung], T0 + timedelta(hours=210))
    assert a.behoben is False and b.behoben is True and c.behoben is True
    assert d.behoben is None and jung.behoben is None                      # Nachlauf (24 h) noch nicht vorbei


def test_ordne_zu_nur_am_selben_host_im_zeitraum():
    e = _ep(1, host="web1", beginn_h=0, dauer_h=2)
    lo.ordne_zu([e], [(T0 + timedelta(hours=1), "web1", "dienst-neustart:nginx", "z1"),
                      (T0 + timedelta(hours=1), "web2", "dienst-neustart:nginx", "z2"),
                      (T0 + timedelta(hours=5), "web1", "host-neustart", "z3"),
                      (T0 - timedelta(hours=1), "web1", "paket-install", "z4")])
    assert e.massnahmen == {"dienst-neustart:nginx": ["z1"]}


def _bewertet(eps):
    for e in eps:
        e.behoben = e.behoben if e.behoben is not None else True
    return lo.bewerte(eps)


def test_basisrate_und_bestaetigt_nur_ueber_ihr():
    eps = []
    for i in range(20):                        # ohne Maßnahme: 4 von 20 behoben
        e = _ep(i, host=f"h{i}")
        e.behoben = i < 4
        eps.append(e)
    for i in range(12):                        # mit Neustart: 11 von 12 behoben
        e = _ep(100 + i, host=f"n{i}", massnahme="dienst-neustart:x")
        e.behoben = i < 11
        eps.append(e)
    w = next(z for z in lo.bewerte(eps) if z["ebene"] == "problem" and z["massnahme"] == "dienst-neustart:x")
    assert w["modus"] == "bestaetigt" and w["n"] == 12 and w["behoben"] == 11 and w["basis_n"] == 20
    assert w["quote_unten"] > w["basisrate"] and len(w["belege"]) == 10


def test_ohne_vorsprung_nur_test_wenig_daten_zu_wenig_und_nie_geholfen_strittig():
    eps = []
    for i in range(10):
        e = _ep(i, host=f"h{i}")
        e.behoben = True                       # verschwindet ohnehin von selbst
        eps.append(e)
    for i in range(10):
        e = _ep(50 + i, host=f"m{i}", massnahme="dienst-neustart:x")
        e.behoben = True
        eps.append(e)
    for i in range(2):
        e = _ep(80 + i, host=f"k{i}", massnahme="paket-install")
        e.behoben = True
        eps.append(e)
    for i in range(3):
        e = _ep(90 + i, host=f"s{i}", massnahme="host-neustart")
        e.behoben = False
        eps.append(e)
    modi = {z["massnahme"]: z["modus"] for z in lo.bewerte(eps) if z["ebene"] == "problem"}
    assert modi == {"dienst-neustart:x": "test", "paket-install": "zu_wenig", "host-neustart": "strittig"}


def test_zusammenfassung_zaehlt_offen_und_aufgeraeumt():
    a, b = _ep(1), _ep(2, dauer_h=None)
    b.aufgeraeumt = True
    lo.setze_behoben([a, b], T0 + timedelta(days=3))
    z = lo.zusammenfassung([a, b], lo.bewerte([a, b]), T0 + timedelta(days=3))
    assert z["episoden"] == 2 and z["offen"] == 1 and z["davon_aufgeraeumt"] == 1 and z["ohne_massnahme"] == 1


def test_fuer_findet_problem_und_regel_und_nennt_leeres():
    stand = {"zeit": "t", "wege": [
        {"ebene": "problem", "problem": "host:web1.example.com", "massnahme": "dienst-neustart:nginx"},
        {"ebene": "regel", "problem": "I-HOST", "massnahme": "host-neustart"}]}
    assert len(lo.fuer(stand, host="web1.example.com")["wege"]) == 1
    assert len(lo.fuer(stand, regel="I-HOST")["wege"]) == 1
    assert lo.fuer(stand, schluessel="muster:x")["grund"]
    assert "noch nicht gelernt" in lo.fuer(None)["grund"]


def test_wilson_vorsichtig_bei_kleinem_n():
    assert lo.wilson_unten(2, 2) < lo.wilson_unten(20, 20) < 1.0
    assert lo.wilson_unten(0, 0) == 0.0


def test_vi_weg_fuer_vi_solved():
    w = {"problem": "host:web1.example.com", "massnahme": "dienst-neustart:nginx", "n": 12, "behoben": 11,
         "quote": 0.857, "quote_unten": 0.64, "basisrate": 0.23, "basis_n": 20, "grund": "untere Grenze 64 % über 23 %",
         "belege": ["i1", "i2"], "beispiel": "sudo: x : COMMAND=/usr/bin/systemctl restart nginx"}
    a = lo.vi_weg(w)
    assert a["name"] == "loesung-host-web1-example-com-dienst-neustart-nginx"
    assert a["triggers"] == ["host:web1.example.com"] and a["steps"] == ["dienst-neustart:nginx"]
    assert "systemctl restart nginx" in a["instructions"] and "i1, i2" in a["instructions"] and "evidence" not in a
    assert lo.vi_weg({**w, "problem": "muster:abc"})["triggers"] == ["fehler:muster:abc"]
