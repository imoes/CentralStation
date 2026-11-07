"""Ein Incident ist ein Objekt der Suche: sein Filter in der einen Filtersprache trifft genau seine
Nachrichten (Nutzer 07.11.)."""
from datetime import datetime, timedelta, timezone

from app.services.log_watch import filter as flt
from app.services.log_watch import incident_objekt as io
from app.services.log_watch.regelwerk import passt


def test_filter_je_art():
    assert io.filter_von("log_cluster", "a", {"template_key": "k1", "patterns": [{"template_key": "k2"}]}) \
        == "(muster:k1 OR muster:k2)"
    assert io.filter_von("log_cluster", "a", {"template_key": "k1"}) == "muster:k1"
    assert io.filter_von("fleet", "a", {"service": "Filesystem /var", "hosts": ["b", "a"]}) \
        == 'dienst:"Filesystem /var" (host:a OR host:b)'
    assert io.filter_von("critical", "a.example.com", {}) == "host:a.example.com signal:schaden"
    assert io.filter_von("host", "a", None, kanon=lambda h: h + ".example.com") == "host:a.example.com"
    # ohne Schlüssel: benannt kein Filter (der Lauf zählt ihn unter ohne_filter)
    assert io.filter_von("log_cluster", "a", {}) is None
    assert io.filter_von("unbekannt", "a", {}) is None


def test_filter_trifft_was_er_soll():
    f = flt.bedingung(io.filter_von("fleet", "a", {"service": "Filesystem /var", "hosts": ["a", "b"]}))
    assert passt(f, {"dienst:Filesystem /var", "host:a"}, {})
    assert not passt(f, {"dienst:Filesystem /var", "host:c"}, {})
    assert not passt(f, {"dienst:Filesystem /", "host:a"}, {})
    f = flt.bedingung(io.filter_von("log_cluster", "x", {"template_key": "k1", "patterns": [{"template_key": "k2"}]}))
    assert passt(f, {"muster:k2", "host:z"}, {}) and not passt(f, {"muster:k3"}, {})


def test_fenster_mit_vorlauf_und_rueckblick():
    jetzt = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    seit, bis = io.fenster(jetzt - timedelta(hours=1), None, jetzt)
    assert seit == jetzt - timedelta(hours=1) - io.VORLAUF and bis == jetzt
    seit, bis = io.fenster(jetzt - timedelta(days=5), jetzt - timedelta(hours=2), jetzt)
    assert seit == jetzt - io.RUECKBLICK and bis == jetzt - timedelta(hours=2)


def test_incident_ist_ein_knoten_im_fehler_netzplan():
    from app.services.log_watch import suche
    b = [{"key": "netz:intern", "doc_count": 900}, {"key": "incident:7f3a", "doc_count": 40},
         {"key": "host:a", "doc_count": 30}]
    k = suche.knoten_waehlen(b, 30, set(), 0, arten=suche.FEHLER_ARTEN, je_art=suche.FEHLER_JE_ART)
    assert [x["id"] for x in k] == ["incident:7f3a", "host:a"]


def test_offenes_ticket_am_incident_heisst_behandelt():
    """N2: Nachrichten im Filter eines Incidents mit offenem Ticket tragen signal:behandelt, mit Grund."""
    from app.services.log_watch import raster
    assert io.ticket_von([{"type": "error_cluster"}, {"type": "ticket", "key": "IMIT-7"}]) == "IMIT-7"
    assert io.ticket_von([{"type": "resolution"}]) is None
    raster.setze_behandelt([(flt.bedingung("muster:k1"), "Ticket IMIT-7 am Incident „x“ (behandelt)")])
    try:
        tags, gr = raster._behandelt(["muster:k1", "host:a"], {}, ["R2"])
        assert "signal:behandelt" in tags and gr[-1].startswith("Ticket IMIT-7")
        tags, gr = raster._behandelt(["muster:k9"], {}, [])
        assert "signal:behandelt" not in tags and gr == []
    finally:
        raster.setze_behandelt([])
