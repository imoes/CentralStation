"""Feldbedeutungen in der Datenbank (Nutzer 07.11.): Grundbestand deckt jedes Feld der Historie und jedes
Signal; die KI schlägt nach und legt nur Neues an; undokumentierte Hashtag-Arten werden benannt."""
from app.services.log_watch import felder
from app.services.log_watch.historie import MAPPING
from app.services.log_watch.regelwerk import SIGNALE


def test_jedes_feld_der_historie_ist_im_grundbestand():
    fehlt = sorted(set(MAPPING["properties"]) - set(felder.FELDER))
    assert fehlt == [], f"Feld ohne Bedeutung in felder.FELDER: {fehlt}"


def test_jedes_signal_ist_im_grundbestand():
    assert sorted(set(SIGNALE) - set(felder.SIGNALE)) == []


def test_grundbestand_namen_mit_art():
    g = {e["name"]: e for e in felder.grundbestand()}
    assert g["feld:zeit"]["art"] == "feld" and "(fest)" in g["feld:zeit"]["bedeutung"]
    assert g["hashtag:regel"]["beispiel"] == "regel:533" and g["signal:neu"]["art"] == "signal"
    assert felder.name_von("hashtag", "regel") == "hashtag:regel" == felder.name_von("hashtag", "hashtag:regel")


def test_undokumentierte_arten_werden_benannt():
    assert felder.undokumentiert_aus(["host", "neuart", "neuart", ""], {"hashtag:host"}) == ["neuart"]


def test_ki_bekommt_nachschlagen_anlegen_und_websuche():
    from app.services.log_watch import trichter
    for w in ("lw_feld", "lw_feld_anlegen", "web_suche", "web_seite"):
        assert w in trichter.TRICHTER_WERKZEUGE
    assert "lw_feld" in trichter.SYSTEM and "SKILL" not in trichter.SYSTEM     # nachschlagen statt Prompt


def test_hashtag_mit_wert_meint_die_art():
    """Gemessen 07.11.: das Modell fragt lw_feld(name="regelgruppe:syscheck")."""
    assert "hashtag:regelgruppe" in felder.kandidaten("regelgruppe:syscheck")
    assert "signal:neu" in felder.kandidaten("signal:neu") and "hashtag:regel" in felder.kandidaten("regel")


def test_quellfeld_antwort_und_prompt():
    assert felder.lies_antwort('```json\n{"bedeutung": "Name des Syslog-Dienstes", "sicher": true}\n```') == \
        {"bedeutung": "Name des Syslog-Dienstes", "sicher": True}
    assert felder.lies_antwort('{"bedeutung": "x"}') is None and felder.lies_antwort("kein json") is None
    p = felder.quellfeld_prompt("graylog", "servicename", "string", "entitaet",
                                [{"wert": "sshd-session", "anzahl": 56}])
    assert "servicename" in p and "'sshd-session' (56)" in p
    assert "quellfeld" in felder.ARTEN and "quellfeld:graylog.servicename" in felder.kandidaten("graylog.servicename")


def test_ki_korrigiert_nichts():
    import asyncio
    assert "abgelehnt" in asyncio.run(felder.korrigiere("hashtag:regel", "egal egal egal", "x", "ki"))
    assert "KEINE Bedeutung einzelner Werte" in felder.PROMPT_QUELLFELD


def test_feldbeschreibung_nur_mit_geprueftem_zitat():
    """Nutzer 07.11.: „Antworte nur mit Fakten, die du belegen kannst — wie im Recherche-Tool mit geprüften Zitaten“."""
    quelle = felder.quellfeld_prompt("checkmk", "host_state", "long", "kategorie", [{"wert": 0, "anzahl": 900}])
    ok, gr = felder.pruefe_zitate("Zustand des Hosts", ["host_state"], quelle)
    assert ok == ["host_state"] and gr == []
    # erfundene Code-Zuordnung (gemessen: host_state mit Service-Codes) ohne Zitat → abgelehnt
    ok, gr = felder.pruefe_zitate("Statuscode 0=OK, 1=Warning", ["host_state"], quelle)
    assert any("0=OK" in g for g in gr)
    ok, gr = felder.pruefe_zitate("irgendwas", ["steht nirgends"], quelle)
    assert ok == [] and "kein geprüftes Zitat" in gr


def test_feld_anlegen_belege_gegen_gelesene_seite():
    import asyncio

    from app.services.log_watch import websuche
    websuche.merke("https://docs.example.org/x", "The field servicename holds the syslog program name.")
    ok, gr = asyncio.run(felder.pruefe_belege_feld([
        {"url": "https://docs.example.org/x", "zitat": "holds the syslog program name"},
        {"url": "https://docs.example.org/x", "zitat": "steht da nicht drin"},
        {"url": "https://ungelesen.example.org/", "zitat": "irgendein Zitat hier"}]))
    assert [b["zitat"] for b in ok] == ["holds the syslog program name"]
    assert any("nicht wörtlich" in g for g in gr) and any("erst mit web_seite" in g for g in gr)



def test_web_prompt_verlangt_zitate_aus_der_seite():
    assert "wörtlich auf der Seite" in felder.PROMPT_QUELLFELD_WEB and "letzten Beitrag" in felder.PROMPT_QUELLFELD_WEB
    assert felder.WEB_JE_LAUF >= 1


def test_neue_hashtag_art_ist_eigenschaft_ohne_wert_und_kein_synonym():
    """Gemessen 07.11.: die KI legte hashtag:standort (= ort) und komponente-sensecommon (Wert im Namen) an."""
    from app.services.log_watch.felder import artname_fehler
    assert artname_fehler("komponente") == "" and artname_fehler("ursache") == ""
    assert "komponente:sensecommon" in artname_fehler("komponente-sensecommon")
    assert "nimm ort" in artname_fehler("standort")
    assert "festes Feld" in artname_fehler("host") and "Art des Systems" in artname_fehler("thema")
