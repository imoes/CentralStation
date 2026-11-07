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
