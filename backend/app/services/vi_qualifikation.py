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

Frageformen wechseln (Vorschlag der vi-mcp-Session, vom Nutzer freigegeben 18.11.2026): je Eintragsart FORMEN Fragesätze; welcher
Satz gilt, bestimmt der Hash des Eintragsschlüssels (`form`) — reihum über die Einträge verteilt, für denselben Eintrag aber stabil,
damit ein erneutes vi_learn mit demselben key dieselben Fragen trägt. Gleich gebaute Fragen über alle Einträge geben einem späteren
Fine-Tuning wenig Vielfalt; dass die Vielfalt dort hilft, ist eine Hypothese der vi-mcp-Session, nicht gemessen.

Die Fragen nennen ihr Subjekt kurz (höchstens SUBJEKT_WORTE Wörter): Zwei Fragen mit langem gemeinsamem Subjekt hätten eine
Wortüberlappung (Jaccard) ≥ 0,5 und würden als „too close to the probe“ verworfen (qa_pairs.HOLD_OUT_JACCARD).

Ältere Server (≤ 0.69.2) lesen nur die Felder, die sie kennen (vi_learn-Handler mit args.get, gelesen in 0.68.0 tools.py) — die
Qualifizierung wird dort ignoriert, das Lernen bleibt gültig.

Rein, ohne Abhängigkeiten — testbar und im vi-mcp-Image gegen qa_pairs.check prüfbar.
"""
from __future__ import annotations

import hashlib
import re

ANSWERS = ("how", "why", "what", "who", "where", "rule")
MEMORY = ("fact", "procedure", "episode", "rule")
SUBJEKT_WORTE = 4
FORMEN = 4
#: Grenzen von qa_pairs.check: Frage 15–300 Zeichen, Antwort 2–800
FRAGE_MIN, FRAGE_MAX, ANTWORT_MAX = 15, 300, 800

_SATZ = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")


def subjekt(s: str, worte: int = SUBJEKT_WORTE) -> str:
    """Kurzes Subjekt für die Frage: die ersten Wörter, ohne Anführungszeichen und Satzzeichen am Ende."""
    w = re.sub(r"[\"“”„`']", "", s or "").split()[:worte]
    return " ".join(w).rstrip(" .,:;–—-")


def form(saat, n: int = FORMEN) -> int:
    """Welcher Fragesatz für diesen Eintrag gilt: stabil je Schlüssel (sha256, nicht hash() — der wechselt je Prozess)."""
    return hashlib.sha256(str(saat or "").encode()).digest()[0] % n


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


#: Fragesätze je Eintragsart; ein Satz = eine Frage je Feld, in derselben Reihenfolge. Jeder Satz ist gegen die Regeln von vi-mcp geprüft
#: (tests/test_vi_qualifikation.py): Subjekt genannt, Längen, Jaccard < 0,45 zwischen den Fragen eines Satzes — mit der Wortzerlegung
#: von vi_mcp/qa_pairs (Wörter ab 3 Zeichen, auch „the“, „was“). Das Subjekt (bis 4 Wörter) teilen alle Fragen, deshalb braucht jede
#: Frage rund fünf eigene Wörter. Die erste Fassung (17.11.) hatte Paare mit genau 0,5 — vermutlich die 12 von 200 „too close to the
#: probe“.
BEKANNT_FORMEN = (
    ("Which filter matches the known error “{s}”?", "What relevance level was assigned to “{s}”?",
     "How often did “{s}” repeat without AI analysis?", "In which CentralStation finding was “{s}” recorded?",
     "Word for word, how does the message “{s}” read?"),
    ("With which search query can “{s}” be found again?", "How relevant did CentralStation rate “{s}”?",
     "How many repetitions of “{s}” went by without an AI look?", "Under which finding id is “{s}” filed?",
     "What is the complete title of the error “{s}”?"),
    ("Which host and service filter selects “{s}”?", "What relevance score does “{s}” carry?",
     "How many times did “{s}” recur before any AI looked at it?", "Which AI finding first described “{s}”?",
     "Give the full wording of “{s}”: what does it say?"),
    ("What lookup expression returns the messages of “{s}”?", "Which relevance did the rules give to “{s}”?",
     "Count of “{s}” occurrences that no AI has analysed?", "Where is the finding behind “{s}” stored?",
     "How is the known error “{s}” worded completely?"),
)
FELD_FORMEN = (
    ("What does the field {n} contain?", "For which purpose is {n} used in CentralStation?", "Who determined the meaning of {n}?"),
    ("What is stored in the field {n}?", "Why does CentralStation keep {n} at all?", "Where does the description of {n} come from?"),
    ("Which information does {n} carry?", "How is the field {n} put to use?", "Who wrote down what {n} means?"),
    ("What kind of value holds the field {n}?", "What job does {n} serve?", "Which source defined the meaning of {n}?"),
)
REZEPT_FORMEN = (
    ("What problem does the recipe “{s}” address?", "How was “{s}” solved in the end?",
     "Which check steps confirm the fix for “{s}”?", "From which incident did “{s}” come?"),
    ("Which fault is described in “{s}”?", "What fixed the issue behind “{s}”?",
     "How do you verify afterwards that “{s}” is gone?", "Which incident gave rise to the recipe “{s}”?"),
    ("What went wrong according to “{s}”?", "Which solution does the recipe “{s}” give?",
     "What should be checked once “{s}” is repaired?", "Where did “{s}” originate, by incident id?"),
    ("Which symptom does the recipe “{s}” name?", "What was done to resolve “{s}”?",
     "Which verification steps belong to “{s}”?", "Which incident record is “{s}” linked with?"),
)
GELOEST_FORMEN = (
    ("How was {x} resolved?", "What was the first finding about {x}?", "Which service did the console session for {x} concern?"),
    ("Which action resolved {x} in the console?", "What did the investigation of {x} establish first?", "Which service was affected by {x}?"),
    ("Which steps closed {x}?", "What was found initially when looking into {x}?", "On which service was {x} reported?"),
    ("How did the console session end {x}?", "What was the initial result for {x}?", "Which monitored service belongs to {x}?"),
)


def bekannter_fehler(e: dict, key: str) -> dict:
    """Bekannter Fehler (vi_bruecke.erkenntnis_aus_bekannt) — ein Zustand mit Zahlen: what/fact."""
    titel = (e.get("titel") or "").strip()
    s = subjekt(titel)
    rel, wdh = int(e.get("relevanz") or 0), int(e.get("wiederholungen") or 0)
    f = BEKANNT_FORMEN[form(key)]
    return qualifikation([
        paar(f[0].format(s=s), key),
        paar(f[1].format(s=s), f"Relevanz {rel}"),
        paar(f[2].format(s=s), f"{wdh} Wiederholungen ohne KI"),
        paar(f[3].format(s=s), f"Befund {e.get('befund')}" if e.get("befund") else None),
        paar(f[4].format(s=s), titel),
    ], "what", "fact")


def feldbedeutung(name: str, bedeutung: str, herkunft: str) -> dict:
    """Bedeutung eines Feldes im Feldkatalog (vi_bruecke.erkenntnis_aus_feld) — what/fact."""
    st = saetze(bedeutung)
    f = FELD_FORMEN[form(name)]
    return qualifikation([
        paar(f[0].format(n=name), st[0] if st else None),
        paar(f[1].format(n=name), st[1] if len(st) > 1 else None),
        paar(f[2].format(n=name), f"CentralStation feld_bedeutung ({herkunft})"),
    ], "what", "fact")


def rezept(titel: str, problem: str, loesung: str, schritte: str, incident_id) -> dict:
    """Rezept aus einer Konsolen-Sitzung (recipes.py) — how/procedure."""
    s = subjekt(titel)
    f = REZEPT_FORMEN[form(incident_id or titel)]
    return qualifikation([
        paar(f[0].format(s=s), problem),
        paar(f[1].format(s=s), loesung),
        paar(f[2].format(s=s), schritte),
        paar(f[3].format(s=s), f"Incident {incident_id}" if incident_id else None),
    ], "how", "procedure")


def geloest(external_id: str, zusammenfassung: str, host: str = "", dienst: str = "") -> dict:
    """Als gelöst markierte Konsolen-Sitzung (api/feed.py) — ein Ereignis mit Lösung: how/episode."""
    st = saetze(zusammenfassung)
    f = GELOEST_FORMEN[form(external_id)]
    x = subjekt(external_id)          # ein langer Dienstname in cmk:<host>:<dienst> hätte sonst alle Fragen zu nah gemacht
    return qualifikation([
        paar(f[0].format(x=x), zusammenfassung),
        paar(f[1].format(x=x), st[0] if len(st) > 1 else None),
        # Subjekt = external_id: das Dokument hat den Host nur als Teil von cmk:<host>:<dienst> (ein Token), die Regel
        # „names a subject“ erkennt den Host allein darum nicht (gemessen 17.11. gegen qa_pairs.check)
        paar(f[2].format(x=x), dienst if host and dienst else None),
    ], "how", "episode")


def loesungsweg() -> dict:
    """Bestätigter Lösungsweg als Erkenntnis (loesungen.py, nur wenn der Server vi_solved nicht kennt) — how/procedure.
    Ohne Paare: der Text ist eine Anleitung aus einem Sprachmodell, feste Vorlagen hätten nichts Belegbares zu fragen."""
    return qualifikation([], "how", "procedure")
