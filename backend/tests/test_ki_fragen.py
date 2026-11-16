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


# Nutzer 16.11.2026: „bei der Mail Kurze Frage der KI: Kritische Kernelmeldung gehäuft: cue0111 … muss der incident
# auch angezeigt werden. Sonst kann man nicht entscheiden.“ — Kontext gekürzt aus Incident 48f0194e (DB, 16.11.).
_CUE = {"id": "48f0194e-9272-423b-8332-5d20213f107b", "host": "cue0111.example.com", "nachrichten": 3,
        "titel": "Kritische Kernelmeldung gehäuft: cue0111.example.com · 1 Hosts · 3 Meldungen",
        "beginn": datetime(2026, 11, 15, 16, 44, tzinfo=timezone.utc),
        "geschlossen": datetime(2026, 11, 16, 0, 19, tzinfo=timezone.utc),
        "kontext": {"type": "error_cluster", "hosts": ["cue0111.example.com"], "regel": "I-KRITISCH",
                    "reasons": ["Regel I-KRITISCH „Kritische Kernelmeldung gehäuft“: quelle:graylog signal:schaden — "
                                "3 fenster (mindestens 3) in 360 min"],
                    "resolved_reason": "Regel I-KRITISCH: 360 min unter den Mindestwerten",
                    "examples": ["cue0111 kernel: LayoutChampApp[3794845]: segfault at 51000000 ip 0000000051000000",
                                 "cue0111 kernel: LayoutChampApp[3762913]: segfault at 7fd81b00e1 <x>",
                                 "drei", "vier"],
                    "triage": {"titel": "Häufte Segfaults von LayoutChampApp auf cue0111 (CUE Print)",
                               "zusammenfassung": "LayoutChampApp stürzt wiederholt mit Segfaults ab.",
                               "vermutete_ursache": "Vermutung: fehlerhafte Speicherzugriffe", "konfidenz": "mittel"}}}


def test_auszug_hat_alles_zum_entscheiden():
    a = kf.incident_auszug(_CUE, "https://cs")
    assert a["url"] == "https://cs/feed?incident=48f0194e-9272-423b-8332-5d20213f107b"
    assert a["dauer_min"] == 455 and a["nachrichten"] == 3 and a["hosts"] == ["cue0111.example.com"]
    assert a["gemeldet_weil"][0].startswith("Regel I-KRITISCH") and "Mindestwerten" in a["geschlossen_weil"]
    assert a["ki"]["titel"].startswith("Häufte Segfaults") and a["ki"]["konfidenz"] == "mittel"
    assert len(a["beispiele"]) == 3 and "LayoutChampApp" in a["beispiele"][0]


def test_mail_zeigt_den_incident_vor_den_knoepfen():
    f = {"id": "f1", "frage": kf.frage_text(_CUE), "incident": kf.incident_auszug(_CUE, "https://cs")}
    h = kf.mail_html(f, {w: f"https://cs/api/ki-fragen/f1/antwort?wahl={w}&t=x" for w in kf.WAHL})
    assert h.index("Häufte Segfaults") < h.index("ging von selbst weg")
    for teil in ("Gemeldet, weil", "Geschlossen, weil", "Mindestwerten", "segfault at 51000000", "455 min",
                 "/feed?incident=48f0194e", "Fehler-Netzplan"):
        assert teil in h, teil
    assert "&lt;x&gt;" in h and "<x>" not in h                     # Logzeilen escaped
    assert h.count("<a href=") == len(kf.WAHL) + 2                 # Antworten + Incident + Netzplan


def test_auszug_ohne_kontext_benennt_die_luecken():
    a = kf.incident_auszug({"id": "i", "host": "h", "titel": "t"}, "https://cs")
    assert a["ki"] is None and a["beispiele"] == [] and a["dauer_min"] is None
    h = kf.incident_html(a)
    assert "keine gespeichert" in h and "kein Grund gespeichert" in h and "Dauer unbekannt" in h
