"""Websuche nur bereinigt (keine internen Namen nach außen) und nur öffentliche Seiten (Nutzer 07.11.)."""
from app.services.log_watch import websuche as ws


def test_interne_namen_werden_entfernt_fehlercodes_bleiben():
    q, weg = ws.bereinige("cue0148.example.com ORA-12541 TNS no listener at host 10.0.188.48 port 1521 "
                          "user cci@example.org token=abc123 /home/mutkluge/app id 4ec1a4de-047a-4113-9b91-d225dcd248d3")
    assert "ORA-12541" in q and "TNS no listener" in q
    for intern in ("cue0148", "example.com", "10.0.188.48", "example", "abc123", "mutkluge", "4ec1a4de"):
        assert intern not in q, intern
    assert {"IP", "E-Mail", "Geheimnis", "UUID", "interner Name", "Benutzerpfad"} <= set(weg)


def test_hausschema_hostnamen_aber_keine_codes():
    q, weg = ws.bereinige("nsa383 PoE thread 1015 ms AH01114 docker0218 kernel segfault")
    assert "nsa383" not in q and "docker0218" not in q and "AH01114" in q and "segfault" in q


def test_nur_oeffentliche_seiten():
    assert ws.oeffentlich("https://stackoverflow.com/questions/1") is None
    for intern in ("http://llm-host03.example.com:8080/x", "http://10.0.0.1/", "http://localhost/", "file:///etc/passwd",
                   "http://intranet/", "https://wiki.example.org/a"):
        assert ws.oeffentlich(intern), intern


def test_text_aus_html():
    t = ws.text_aus_html("<html><script>x()</script><h1>Fehler</h1><p>ORA-12541 &amp; mehr</p></html>")
    assert "x()" not in t and "Fehler" in t and "ORA-12541 & mehr" in t


def test_ohne_grund_keine_suche():
    import asyncio
    assert "warum" in asyncio.run(ws.suche("ORA-12541 TNS", ""))["fehler"]
    assert "keine sinnvolle Anfrage" in asyncio.run(ws.suche("cue0148.example.com 10.1.1.1", "unklar"))["fehler"]


def test_stackexchange_urls():
    assert ws.stackexchange("https://stackoverflow.com/questions/13358656/oracle-client-ora-12541") == ("stackoverflow", "13358656")
    assert ws.stackexchange("https://unix.stackexchange.com/questions/42/x") == ("unix", "42")
    assert ws.stackexchange("https://serverfault.com/questions/7") == ("serverfault", "7")
    assert ws.stackexchange("https://docs.oracle.com/x") is None


def test_lange_seite_behaelt_das_ende():
    """Nutzer 07.11.: im Forum ist der letzte Beitrag meist die Lösung — die Kürzung behält das Ende."""
    t = "FRAGE " + "x" * 20000 + " LOESUNG: restart the listener"
    k = ws.kuerze_mit_ende(t, 6000)
    assert k.startswith("FRAGE") and k.endswith("LOESUNG: restart the listener") and len(k) <= 6000
