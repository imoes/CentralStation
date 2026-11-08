"""Tagesmuster eines Dateisystems (Nutzer 08.11.: /home/ngrc läuft voll und wird bereinigt)."""
from datetime import datetime, timedelta, timezone

from app.services.log_watch import tagesmuster as tm

AUSGABE = ("Used: 54.47% - 428 GiB of 786 GiB (warn/crit at 90.40%/95.20% used), trend per 1 day 0 hours: +126 GiB, "
           "trend per 1 day 0 hours: +16.07%, Time left until disk full: 2 days 19 hours "
           "(warn/crit below 3 days 0 hours/2 days 0 hours)(!)")
JETZT = datetime(2026, 11, 6, 13, 6, tzinfo=timezone.utc)


def _reihe(tage=5, hoch=68.0, tief=20.0, heute_tief=None):
    """Stündliche Werte: 05:00 tief, 14:00 hoch, abends bereinigt (wie cue0112, 01.–06.11.)."""
    out = []
    start = JETZT.replace(hour=0, minute=0) - timedelta(days=tage)
    for h in range(tage * 24 + 13):
        t = start + timedelta(hours=h)
        stunde = t.hour
        v = tief + (hoch - tief) * (max(0, min(stunde, 14) - 5) / 9) if stunde <= 19 else tief + 5
        out.append((t, v if heute_tief is None or t < JETZT - timedelta(hours=24) else max(v, heute_tief)))
    return out


def test_trendalarm_nur_ohne_prozentgrenze():
    assert tm.trend_alarm(AUSGABE) == {"used": 54.47, "warn": 90.4, "crit": 95.2}
    assert tm.trend_alarm(AUSGABE.replace("Used: 54.47%", "Used: 91.00%")) is None      # Prozentgrenze erreicht
    assert tm.trend_alarm("Used: 54.47% - 428 GiB of 786 GiB (warn/crit at 90.40%/95.20% used)") is None
    assert tm.ist_dateisystem("Filesystem /home/ngrc") and not tm.ist_dateisystem("CPU load")


def test_tagesmuster_erkannt():
    e = tm.tagesmuster(_reihe(), JETZT, 54.47)
    assert e["erkannt"] and e["tage_mit_zyklus"] >= 3 and "bekanntes Tagesmuster" in e["grund"]


def test_echter_fehler_ueber_hoechststand_oder_ohne_bereinigung():
    e = tm.tagesmuster(_reihe(), JETZT, 80.0)
    assert not e["erkannt"] and "über dem bisherigen Tageshöchststand" in e["grund"]
    e = tm.tagesmuster(_reihe(heute_tief=55.0), JETZT, 60.0)
    assert not e["erkannt"] and "Bereinigung ausgeblieben" in e["grund"]
    e = tm.tagesmuster([(JETZT - timedelta(hours=h), 50.0) for h in range(120)], JETZT, 50.0)
    assert not e["erkannt"] and "kein Tagesmuster" in e["grund"]
    assert tm.tagesmuster([], JETZT, 50.0)["grund"] == "kein Verlauf aus CheckMK"


def test_raster_und_regel_tagesmuster_ist_routine():
    """CRIT eines Dateisystems im Tagesmuster: kein Schaden, kein dringend → N1-TAG vor R1; außerhalb: relevant."""
    from app.services.log_watch.definition import STANDARD
    from app.services.log_watch.hosts import Hostverzeichnis
    from app.services.log_watch.raster import bewerte_checkmk
    from app.services.log_watch.regelwerk import bewerte
    p = {"host": "cue0112.example.com", "service": "Filesystem /home/ngrc", "severity": "critical",
         "output": AUSGABE, "metadata": {"criticality": "prod"}}
    im = bewerte_checkmk({}, [{**p, "tagesmuster": {"erkannt": True, "grund": "bekanntes Tagesmuster: …"}}],
                         Hostverzeichnis(), STANDARD).treffer[0]
    assert "signal:tagesmuster" in im.hashtags and "signal:schaden" not in im.hashtags and im.kriterien[0] == "N1"
    aus = bewerte_checkmk({}, [{**p, "tagesmuster": {"erkannt": False, "grund": "Bereinigung ausgeblieben: …"}}],
                          Hostverzeichnis(), STANDARD).treffer[0]
    assert "signal:tagesmuster" not in aus.hashtags and aus.urteil == "relevant"
    assert any("Bereinigung ausgeblieben" in g for g in aus.gruende)
    assert bewerte(STANDARD.regeln, {"signal:tagesmuster", "zustand:crit", "kritikalitaet:prod"}, {}).regel == "N1-TAG"
