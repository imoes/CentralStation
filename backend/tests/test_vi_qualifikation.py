"""Speichern = taggen + qualifizieren (vi-mcp 0.69.3): CentralStation schickt Frage-Antwort-Paare und answers/memory gleich mit
(vi-mcp-Session 17.11.2026, Nutzer „ja“). Gegen die festen Regeln von vi-mcp selbst (qa_pairs.select) an 40 echten bekannten
Fehlern, 15 Feldbedeutungen, einem Rezept und einer gelösten Sitzung gemessen (17.11.): 200/200, 41/42, 3/3, 2/3 behalten.
Seit 18.11. wechseln die Frageformen je Eintrag (FORMEN Sätze, Wahl über den Schlüssel-Hash)."""
import asyncio
import re

import pytest

from app.services import vi_qualifikation as vq
from app.services.log_watch import vi_bruecke as vb

BEKANNT = {"titel": "mucads02-v: DNS-Check CRITICAL bei 0,308 s – Auflösung korrekt, Muster bekannt (7. Host)",
           "status": "aktiv", "befund": "ki:ec8c5f2f87", "relevanz": 1, "wiederholungen": 4,
           "hosts": ["vm-dhwad1.dhw.local"], "hashtags": ["host:vm-dhwad1.dhw.local", "thema:dns"],
           "filter": 'host:vm-dhwad1.dhw.local dienst:"DNS monitoring.example.com"',
           "fehler": "vm-dhwad1.dhw.local|dienst:DNS monitoring.example.com"}


def _dokument(text, quelle):
    """Wie vi_mcp/dataset._answer: Text, Leerzeile, Source."""
    return f"{text}\n\nSource: {quelle}"


def test_bekannter_fehler_jede_antwort_steht_woertlich_im_dokument():
    text, tags, quelle, w, key = vb.erkenntnis_aus_bekannt(BEKANNT)
    q = vq.bekannter_fehler(BEKANNT, key)
    assert (q["answers"], q["memory"]) == ("what", "fact")
    assert len(q["pairs"]) == 5
    doc = _dokument(text, quelle)
    for p in q["pairs"]:
        assert p["answer"] in doc, p
        assert "“mucads02-v: DNS-Check CRITICAL bei”" in p["question"]    # Subjekt kurz genannt, nie „the document“
        assert 15 <= len(p["question"]) <= 300


_WORT = re.compile(r"[\w./:-]{3,}")          # wie vi_mcp/qa_pairs.WORD und words()


def _jaccard(a, b):
    x = {w.lower().strip(".:/-") for w in _WORT.findall(a)} - {""}
    y = {w.lower().strip(".:/-") for w in _WORT.findall(b)} - {""}
    return len(x & y) / max(1, len(x | y))


def _ohne_nahe_fragen(ps):
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            j = _jaccard(a["question"], b["question"])
            assert j < 0.45, (a["question"], b["question"], j)       # vi-mcp verwirft ab 0,5; Abstand für längere Subjekte


def _schluessel_fuer_jede_form():
    """Je Form einen Schlüssel, der sie wählt — damit jeder Satz einmal durch die echten Bauer läuft."""
    k = {}
    for i in range(400):
        k.setdefault(vq.form(f"k{i}"), f"k{i}")
    assert sorted(k) == list(range(vq.FORMEN))
    return [k[f] for f in range(vq.FORMEN)]


def test_kurzes_subjekt_haelt_die_fragen_auseinander_in_jeder_form():
    """Langes gemeinsames Subjekt → Jaccard ≥ 0,5 → vi-mcp verwirft Paare als „too close to the probe“."""
    for key in _schluessel_fuer_jede_form():
        q = vq.bekannter_fehler(BEKANNT, key)
        assert len(q["pairs"]) == 5
        _ohne_nahe_fragen(q["pairs"])
        _ohne_nahe_fragen(vq.feldbedeutung(key, "Das Feld enthält den Zustand. Es dient zur Alarmierung.", "ki")["pairs"])
        _ohne_nahe_fragen(vq.rezept("CheckMK-Alarm: Storage Pool data", "Pool meldet nicht OK.", "Pool ist intakt.",
                                    "zpool status prüfen", key)["pairs"])
        _ohne_nahe_fragen(vq.geloest(key, "Pool intakt. Snapshot gelöscht.", "h", "Storage Pool data")["pairs"])
        lang = "cmk:docker0218.example.com:Filesystem /var/lib/docker usage on the root volume"
        _ohne_nahe_fragen(vq.geloest(lang, "Platz frei. Images gelöscht.", "docker0218.example.com", "Filesystem")["pairs"])


def test_formen_wechseln_je_eintrag_und_bleiben_je_schluessel_gleich():
    a, b = _schluessel_fuer_jede_form()[:2]
    assert vq.bekannter_fehler(BEKANNT, a) == vq.bekannter_fehler(BEKANNT, a)          # erneutes Lernen: dieselben Fragen
    fa = [p["question"] for p in vq.bekannter_fehler(BEKANNT, a)["pairs"]]
    fb = [p["question"] for p in vq.bekannter_fehler(BEKANNT, b)["pairs"]]
    assert not set(fa) & set(fb)                                                      # andere Form: keine Frage gleich
    for satz in (vq.BEKANNT_FORMEN, vq.FELD_FORMEN, vq.REZEPT_FORMEN, vq.GELOEST_FORMEN):
        assert len(satz) == vq.FORMEN and len({len(f) for f in satz}) == 1           # gleich viele Fragen je Satz
        assert not any(re.search(r"\b(the|this) (document|page|text)\b", q, re.I) for f in satz for q in f)


def test_feldbedeutung_saetze_und_herkunft():
    q = vq.feldbedeutung("quellfeld:checkmk.state", "Das Feld enthält den Zustand. Es dient zur Alarmierung.", "ki")
    assert all("quellfeld:checkmk.state" in p["question"] for p in q["pairs"])
    assert [p["answer"] for p in q["pairs"]] == ["Das Feld enthält den Zustand.", "Es dient zur Alarmierung.",
                                                 "CentralStation feld_bedeutung (ki)"]
    einsatz = vq.feldbedeutung("quellfeld:x", "Nur ein Satz", "mensch")
    assert len(einsatz["pairs"]) == 2                               # fehlender zweiter Satz: kein leeres Paar


def test_rezept_und_geloest_sind_anleitungen_bzw_ereignisse():
    r = vq.rezept("CheckMK-Alarm: Storage Pool data", "Pool meldet nicht OK.", "Pool ist intakt.", "", "48f0194e")
    assert (r["answers"], r["memory"]) == ("how", "procedure") and len(r["pairs"]) == 3   # keine Prüfschritte → kein Paar
    g = vq.geloest("cmk:vpp0221.example.com:Storage Pool data", "Pool intakt. Snapshot gelöscht.",
                   "vpp0221.example.com", "Storage Pool data")
    assert (g["answers"], g["memory"]) == ("how", "episode")
    assert g["pairs"][-1]["answer"] == "Storage Pool data"
    assert "cmk:vpp0221.example.com:Storage Pool data" in g["pairs"][-1]["question"]
    assert vq.loesungsweg() == {"answers": "how", "memory": "procedure"}


def test_lange_antwort_wird_an_wortgrenze_gekuerzt_und_leeres_faellt_weg():
    p = vq.paar("What does the field quellfeld:x contain?", "wort " * 300)
    assert len(p["answer"]) <= vq.ANTWORT_MAX and not p["answer"].endswith(" ")
    assert vq.paar("What does the field quellfeld:x contain?", None) is None
    assert vq.paar("kurz?", "Antwort") is None


def test_answers_und_memory_nur_aus_den_festen_klassen():
    with pytest.raises(ValueError):
        vq.qualifikation([], "when", "fact")


def test_konnektor_schickt_die_felder_im_selben_vi_lerne_aufruf(monkeypatch):
    from app.services.connectors.vi_mcp import ViMcpConnector
    k = ViMcpConnector("http://vi-mcp:8765/mcp", {"token": "t"})
    gesehen = {}

    async def werkzeug(name, arg, timeout=20.0):
        gesehen.update(name=name, arg=arg)
        return "learned erkenntnis:1 · qualified: 3 pairs + probe, answers:what memory:fact"
    monkeypatch.setattr(k, "werkzeug", werkzeug)
    text, tags, quelle, w, key = vb.erkenntnis_aus_bekannt(BEKANNT)
    asyncio.run(k.lerne(text, tags, quelle, w, key, ausgang="schlecht", qualifikation=vq.bekannter_fehler(BEKANNT, key)))
    a = gesehen["arg"]
    assert gesehen["name"] == "vi_lerne" and a["answers"] == "what" and a["memory"] == "fact" and len(a["pairs"]) == 5
    assert a["key"] == key and a["ausgang"] == "schlecht"
    asyncio.run(k.lerne(text, tags, quelle, w))
    assert "pairs" not in gesehen["arg"] and "answers" not in gesehen["arg"]   # ohne Qualifizierung wie bisher
