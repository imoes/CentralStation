"""Der Abschnitt zur Wissensdatenbank steht in der Agenten-Anleitung genau dann,
wenn aikb nutzbar ist.

Beide Richtungen kosten: ohne Abschnitt schlägt Claude bei Server-Fragen kaum nach
und verpasst Runbooks und bekannte Probleme; mit Abschnitt, aber ohne Konnektor,
schickt die Anleitung den Agenten zu einem Werkzeug, das "kein Konnektor
konfiguriert" antwortet — auf jede Frage ein verschwendeter Schritt.
"""
from __future__ import annotations

import inspect

from app.services.userenv_manager import (
    agent_instructions,
    configure_agent_instructions,
    configure_ssh,
)

MARKER = "WISSENSDATENBANK"


def test_section_present_when_kb_available():
    text = agent_instructions("marvin", kb_available=True)
    assert MARKER in text
    assert "search_knowledge_base" in text


def test_section_absent_when_kb_unavailable():
    text = agent_instructions("marvin", kb_available=False)
    assert MARKER not in text
    assert "search_knowledge_base" not in text


def test_default_is_absent():
    """Ohne Angabe wird der Abschnitt weggelassen — nie ein Werkzeug versprechen,
    dessen Verfügbarkeit niemand geprüft hat."""
    assert MARKER not in agent_instructions("marvin")


def test_rest_of_briefing_is_unchanged_by_the_flag():
    """Der Schalter fügt nur hinzu; er darf nichts anderes an der Anleitung ändern."""
    without = agent_instructions("marvin", kb_available=False)
    with_kb = agent_instructions("marvin", kb_available=True)
    assert with_kb.startswith(without)


def test_flag_is_keyword_only_all_the_way_down():
    """Positionell übergeben ließe sich der Wert leicht an die falsche Stelle setzen —
    configure_ssh hat davor schon drei Zeichenketten-Parameter."""
    for fn in (agent_instructions, configure_agent_instructions, configure_ssh):
        p = inspect.signature(fn).parameters["kb_available"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__
        assert p.default is False, fn.__name__
