"""Speichern = taggen + qualifizieren in einem vi_learn-Aufruf (vi-mcp ≥ 0.69.3).

Anlass: vi-mcp-Session 17.11.2026 („Wenn CentralStation Befunde lernt, bitte diese Felder gleich mitschicken“), vom Nutzer
freigegeben („ja“). vi_learn nimmt optional `pairs` (etwa 5 Frage-Antwort-Paare auf Englisch), `answers`
(how|why|what|who|where|rule) und `memory` (fact|procedure|episode|rule), die beiden immer zusammen. Ohne sie verlangt die Antwort
sie nachträglich (QUALIFY …), und das Objekt bleibt bis zum Schlaf des Servers unqualifiziert.

Die Paare entstehen hier aus festen Vorlagen über genau die Felder, aus denen auch der gelernte Text gebaut wird — kein
Sprachmodell. Jede Antwort ist ein wörtlicher Ausschnitt aus Text oder Quelle, damit die festen Regeln von vi-mcp
(qa_pairs.check) sie annehmen: die Frage nennt ihr Subjekt, jeder Name und jede Zahl der Antwort steht im Dokument
(Dokument = Text + „Source: <quelle>“, vi_mcp/dataset._answer). Was die Regeln dennoch verwerfen, nennt vi-mcp in der Antwort
(„rejected: …“) — nichts verschwindet still.

Die Fragen nennen ihr Subjekt kurz (höchstens SUBJEKT_WORTE Wörter): Zwei Fragen mit langem gemeinsamem Subjekt hätten eine
Wortüberlappung (Jaccard) ≥ 0,5 und würden als „too close to the probe“ verworfen (qa_pairs.HOLD_OUT_JACCARD).

Ältere Server (≤ 0.69.2, z. B. die Prod-VI) lesen nur die Felder, die sie kennen (vi_learn-Handler mit args.get, gelesen in
0.68.0 tools.py) — die Qualifizierung wird dort ignoriert, das Lernen bleibt gültig.

Rein, ohne Abhängigkeiten — testbar und im vi-mcp-Image gegen qa_pairs.check prüfbar.
"""
from __future__ import annotations

import re

ANSWERS = ("how", "why", "what", "who", "where", "rule")
MEMORY = ("fact", "procedure", "episode", "rule")
SUBJEKT_WORTE = 4
#: Grenzen von qa_pairs.check: Frage 15–300 Zeichen, Antwort 2–800
FRAGE_MIN, FRAGE_MAX, ANTWORT_MAX = 15, 300, 800

_SATZ = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")


def subjekt(s: str, worte: int = SUBJEKT_WORTE) -> str:
    """Kurzes Subjekt für die Frage: die ersten Wörter, ohne Anführungszeichen und Satzzeichen am Ende."""
    w = re.sub(r"[\"“”„`']", "", s or "").split()[:worte]
    return " ".join(w).rstrip(" .,:;–—-")


def saetze(s: str) -> list[str]:
    return [x.strip() for x in _SATZ.split((s or "").strip()) if x.strip()]


def paar(frage: str, antwort) -> dict | None:
    """Ein Paar, oder None, wenn die Antwort fehlt oder die Längen nicht passen (dann gar nicht erst schicken)."""
    a = str(antwort or "").strip()
    if len(a) > ANTWORT_MAX:
        a = a[:ANTWORT_MAX].rsplit(" ", 1)[0]
    if len(a) < 2 or not FRAGE_MIN <= len(frage) <= FRAGE_MAX:
        return None
    return {"question": frage, "answer": a}


def qualifikation(paare: list, answers: str, memory: str) -> dict:
    """Die drei optionalen vi_learn-Felder. answers/memory immer zusammen (vi-mcp lehnt eines allein ab)."""
    if answers not in ANSWERS or memory not in MEMORY:
        raise ValueError(f"answers ∈ {ANSWERS}, memory ∈ {MEMORY}")
    q: dict = {"answers": answers, "memory": memory}
    p = [x for x in paare if x][:8]
    if p:
        q["pairs"] = p
    return q


def bekannter_fehler(e: dict, key: str) -> dict:
    """Bekannter Fehler (vi_bruecke.erkenntnis_aus_bekannt) — ein Zustand mit Zahlen: what/fact."""
    titel = (e.get("titel") or "").strip()
    s = subjekt(titel)
    rel, wdh = int(e.get("relevanz") or 0), int(e.get("wiederholungen") or 0)
    return qualifikation([
        paar(f"Which filter matches the known error “{s}”?", key),
        paar(f"What relevance was assigned to “{s}”?", f"Relevanz {rel}"),
        paar(f"How often did “{s}” repeat without AI analysis?", f"{wdh} Wiederholungen ohne KI"),
        paar(f"Which CentralStation finding recorded “{s}”?", f"Befund {e.get('befund')}" if e.get("befund") else None),
        paar(f"What exactly does the known error “{s}” say?", titel),
    ], "what", "fact")


def feldbedeutung(name: str, bedeutung: str, herkunft: str) -> dict:
    """Bedeutung eines Feldes im Feldkatalog (vi_bruecke.erkenntnis_aus_feld) — what/fact."""
    st = saetze(bedeutung)
    return qualifikation([
        paar(f"What does the field {name} contain?", st[0] if st else None),
        paar(f"What is the field {name} used for?", st[1] if len(st) > 1 else None),
        paar(f"Who determined the meaning of the field {name}?", f"CentralStation feld_bedeutung ({herkunft})"),
    ], "what", "fact")


def rezept(titel: str, problem: str, loesung: str, schritte: str, incident_id) -> dict:
    """Rezept aus einer Konsolen-Sitzung (recipes.py) — how/procedure."""
    s = subjekt(titel)
    return qualifikation([
        paar(f"What problem does the recipe “{s}” address?", problem),
        paar(f"How was “{s}” solved?", loesung),
        paar(f"Which check steps belong to the recipe “{s}”?", schritte),
        paar(f"Which incident produced the recipe “{s}”?", f"Incident {incident_id}" if incident_id else None),
    ], "how", "procedure")


def geloest(external_id: str, zusammenfassung: str, host: str = "", dienst: str = "") -> dict:
    """Als gelöst markierte Konsolen-Sitzung (api/feed.py) — ein Ereignis mit Lösung: how/episode."""
    st = saetze(zusammenfassung)
    return qualifikation([
        paar(f"How was {external_id} resolved?", zusammenfassung),
        paar(f"What was the first finding when {external_id} was resolved?", st[0] if len(st) > 1 else None),
        # Subjekt = external_id: das Dokument hat den Host nur als Teil von cmk:<host>:<dienst> (ein Token), die Regel
        # „names a subject“ erkennt den Host allein darum nicht (gemessen 17.11. gegen qa_pairs.check)
        paar(f"Which service was resolved in the console for {external_id}?", dienst if host and dienst else None),
    ], "how", "episode")


def loesungsweg() -> dict:
    """Bestätigter Lösungsweg als Erkenntnis (loesungen.py, nur wenn der Server vi_solved nicht kennt) — how/procedure.
    Ohne Paare: der Text ist eine Anleitung aus einem Sprachmodell, feste Vorlagen hätten nichts Belegbares zu fragen."""
    return qualifikation([], "how", "procedure")
