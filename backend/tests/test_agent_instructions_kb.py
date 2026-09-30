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


def test_timeout_chain_is_ordered():
    """Die innere Grenze muss zuerst greifen, sonst schneidet eine äußere still ab.

    aikb-Tiefensuche < MCP-Werkzeuggrenze < nginx. Eine Tiefensuche macht rund 120
    LLM-Aufrufe und brauchte gemessen 279 s — die frühere Grenze von 300 s lag so knapp
    darüber, dass jede Schwankung sie riss.
    """
    import re
    from pathlib import Path

    from app.services.connectors.aikb import DEEPSEARCH_TIMEOUT_S
    from app.services.userenv_manager import MCP_TOOL_TIMEOUT_S

    assert DEEPSEARCH_TIMEOUT_S >= 900
    assert MCP_TOOL_TIMEOUT_S > DEEPSEARCH_TIMEOUT_S

    conf = Path(__file__).resolve().parents[2] / "nginx" / "nginx.conf.example"
    if conf.is_file():   # im Backend-Image gibt es kein nginx/-Verzeichnis
        api = conf.read_text(encoding="utf-8").split("location /api/ {", 1)[1].split("}", 1)[0]
        read = int(re.search(r"proxy_read_timeout\s+(\d+)s", api).group(1))
        assert read > MCP_TOOL_TIMEOUT_S, "nginx würde den Antwortstrom vor dem Werkzeug kappen"
