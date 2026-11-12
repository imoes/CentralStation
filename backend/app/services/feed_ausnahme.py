"""Feed-Ausnahmen (FeedSearch mit is_exclusion): Eingaben prüfen, bevor die Datenbank sie ablehnt.

Externe Analyse 12.11.2026 (F7): create_feed_exclusion mit einem Namen > 100 Zeichen lieferte einen rohen
asyncpg-Fehler (StringDataRightTruncationError samt komplettem INSERT) an den Client, und die Ausnahme fehlte.
"""
from __future__ import annotations

#: Spaltenlänge FeedSearch.name (models/workflow.py: String(100))
NAME_MAX = 100


def eingabe_fehler(name: str) -> str | None:
    """Klare Meldung statt DB-Fehler, wenn der Name zu lang ist; None = in Ordnung. Rein."""
    n = (name or "").strip()
    if len(n) > NAME_MAX:
        return (f"Name ist {len(n)} Zeichen lang, erlaubt sind {NAME_MAX} — bitte kürzer fassen "
                f"(Details gehören in den query_string)")
    return None
