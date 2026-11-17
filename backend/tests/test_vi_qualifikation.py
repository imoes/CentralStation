"""Speichern = taggen + qualifizieren (vi-mcp 0.69.3): CentralStation schickt Frage-Antwort-Paare und answers/memory gleich mit
(vi-mcp-Session 17.11.2026, Nutzer „ja“). Gegen die festen Regeln von vi-mcp selbst (qa_pairs.select) an 40 echten bekannten
Fehlern, 15 Feldbedeutungen, einem Rezept und einer gelösten Sitzung gemessen (17.11.): 200/200, 41/42, 3/3, 2/3 behalten."""
import asyncio

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


def test_kurzes_subjekt_haelt_die_fragen_auseinander():
    """Langes gemeinsames Subjekt → Jaccard ≥ 0,5 → vi-mcp verwirft Paare als „too close to the probe“."""
    def w(s):
        return {x.lower().strip(".:/-“”") for x in s.split() if len(x) >= 3}
    ps = vq.bekannter_fehler(BEKANNT, "k")["pairs"]
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            j = len(w(a["question"]) & w(b["question"])) / len(w(a["question"]) | w(b["question"]))
            assert j < 0.5, (a["question"], b["question"], j)


def test_feldbedeutung_saetze_und_herkunft():
    q = vq.feldbedeutung("quellfeld:checkmk.state", "Das Feld enthält den Zustand. Es dient zur Alarmierung.", "ki")
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
    assert g["pairs"][-1] == {"question": "Which service was resolved in the console for cmk:vpp0221.example.com:Storage Pool data?",
                              "answer": "Storage Pool data"}
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
