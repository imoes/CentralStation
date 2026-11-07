"""Rückfragen zwischen KI-Instanzen (rueckfrage.py) — reine Teile und Verdrahtung."""
from app.services.log_watch import rueckfrage as rf
from app.services.log_watch import trichter, werkzeuge as wz


def test_zugestellte_frage_traegt_kontext_und_auftrag():
    z = rf.als_zug({"id": "abc", "von": "agent-1", "an_host": "cue092.example.com", "frage": "Siehst du OOM?",
                    "kontext": {"fall": "muster:f3db", "belege": ["graylog:1", "graylog:2"]}})
    assert "Rückfrage abc von agent-1 zu cue092.example.com: Siehst du OOM?" in z
    assert "muster:f3db" in z and "graylog:1, graylog:2" in z and 'lw_antwort(id="abc"' in z


def test_werkzeuge_im_trichter_und_rollen():
    for w in ("lw_frage", "lw_fragen", "lw_antwort"):
        assert w in wz.REGISTRY and w in trichter.TRICHTER_WERKZEUGE
    # fragen und lesen auch über MCP (Konsole) — antworten nur Agenten der Flotte
    assert wz.REGISTRY["lw_frage"][3] is False and wz.REGISTRY["lw_fragen"][3] is False
    assert wz.REGISTRY["lw_antwort"][3] is True
    assert set(rf.STATUS) == {"offen", "zugestellt", "beantwortet", "unbeantwortet", "niemand"}


def test_prompt_nennt_rueckfrage():
    assert "lw_frage(host, frage, belege)" in trichter.SYSTEM and "lw_antwort" in trichter.SYSTEM


def test_feld_doku_ist_aufgabe_der_flotte():
    """Gemessen 07.11.: als eigener Job setzte die Feld-Doku fünfmal aus (Flotte voll)."""
    import inspect
    assert trichter.FELD_DOKU_LEASE.startswith("__") and trichter.FELD_DOKU_ABSTAND_MIN == 30
    assert "feld_doku_als_aufgabe(name)" in inspect.getsource(trichter.agent)
