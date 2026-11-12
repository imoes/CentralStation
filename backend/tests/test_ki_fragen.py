"""KI fragt nach (F6 der externen Analyse 12.11.2026): Auswahl, Signatur, Wirkung, Mail, Konsole."""
import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services import ki_fragen as kf

J = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def _inc(i, n, massnahmen=(), vor_h=2):
    return {"id": i, "host": "h", "titel": f"t{i}", "geschlossen": J - timedelta(hours=vor_h), "nachrichten": n,
            "massnahmen": list(massnahmen)}


def test_kandidaten_hoechstens_drei_am_tag_groesste_zuerst():
    incs = [_inc("a", 5), _inc("b", 50), _inc("c", 10), _inc("d", 40), _inc("e", 1)]
    assert [i["id"] for i in kf.kandidaten(incs, set(), 0, J)] == ["b", "d", "c"]
    assert [i["id"] for i in kf.kandidaten(incs, set(), 2, J)] == ["b"]                 # heute schon 2 gefragt
    assert kf.kandidaten(incs, set(), 3, J) == []


def test_kandidaten_mit_mail_empfaenger_zuerst():
    """12.11.: erste Runde fragte nur Hosts außerhalb jedes CheckMK-Filters (0 Mails) — Empfänger gehen jetzt vor."""
    incs = [_inc("a", 5), _inc("b", 50), _inc("c", 10), _inc("d", 40), _inc("e", 1)]
    assert [i["id"] for i in kf.kandidaten(incs, set(), 0, J, {"a", "e"})] == ["a", "e", "b"]
    assert [i["id"] for i in kf.kandidaten(incs, set(), 2, J, {"a", "e"})] == ["a"]
    assert [i["id"] for i in kf.kandidaten(incs, set(), 0, J, set())] == ["b", "d", "c"]   # ohne Empfänger: wie bisher


def test_kandidaten_ohne_massnahme_ungefragt_und_juenger_24h():
    incs = [_inc("a", 9, ["ticket"]), _inc("b", 8), _inc("c", 7, vor_h=30)]
    assert [i["id"] for i in kf.kandidaten(incs, {"b"}, 0, J)] == []
    assert [i["id"] for i in kf.kandidaten(incs, set(), 0, J)] == ["b"]


def test_signatur_nur_fuer_diese_frage_und_antwort():
    t = kf.token("geheim", "f1", "neustart")
    assert kf.token_gueltig("geheim", "f1", "neustart", t)
    assert not kf.token_gueltig("geheim", "f1", "rauschen", t)
    assert not kf.token_gueltig("geheim", "f2", "neustart", t)
    assert not kf.token_gueltig("anders", "f1", "neustart", t)
    assert not kf.token_gueltig("geheim", "f1", "erfunden", kf.token("geheim", "f1", "erfunden"))


def test_wirkung_erschoepfend():
    assert kf.wirkung("neustart") == {"urteil": "relevant", "relevanz": 2, "kriterien": ["R1"], "massnahme": "neustart"}
    assert kf.wirkung("rauschen") == {"urteil": "nicht_relevant", "relevanz": 0, "kriterien": ["N4"], "massnahme": None}
    assert kf.wirkung("von_selbst") == {"urteil": None, "relevanz": None, "kriterien": [], "massnahme": None}
    with pytest.raises(ValueError):
        kf.wirkung("loeschen")
    assert set(kf.WAHL) == {"neustart", "konfiguration", "ticket", "von_selbst", "rauschen", "weiss_nicht"}


def test_mail_hat_einen_link_je_antwort():
    f = {"id": "f1", "frage": "Was hat geholfen?"}
    h = kf.mail_html(f, {w: f"https://cs/api/ki-fragen/f1/antwort?wahl={w}&t=x" for w in kf.WAHL})
    assert h.count("<a href=") == len(kf.WAHL) and "Konsole" in h


def test_konsole_werkzeuge_und_anweisung():
    q = pathlib.Path("app/api/mcp_server.py").read_text()
    namen = {n.name for n in ast.parse(q).body if isinstance(n, ast.AsyncFunctionDef)}
    assert {"ki_fragen_offen", "ki_frage_beantworten"} <= namen
    b = next(n for n in ast.parse(q).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "ki_frage_beantworten")
    assert "_require_write" in ast.unparse(b)
    assert "ki_fragen_offen()" in pathlib.Path("app/services/userenv_manager.py").read_text()


def test_loesungswege_kennen_ticket_und_antwort_als_massnahme():
    from app.services.log_watch import loesungen as lo
    assert {"ticket", "neustart", "konfiguration"} <= set(lo.ARTEN)
