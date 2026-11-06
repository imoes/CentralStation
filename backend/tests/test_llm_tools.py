"""Werkzeugaufrufe im LLM-Client: Antwort lesen, ungültige Argumente benannt, Modus geprüft."""
import pytest

from app.services import llm_client as lc


def test_werkzeug_aufrufe_lesen():
    msg = {"tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "lw_kriterien", "arguments": '{"hours": 3}'}},
        {"id": "c2", "type": "function", "function": {"name": "lw_suche", "arguments": "{kaputt"}},
        {"type": "function", "function": {"name": "x", "arguments": "[1]"}},
    ]}
    a = lc.werkzeug_aufrufe(msg)
    assert a[0] == {"id": "c1", "name": "lw_kriterien", "argumente": {"hours": 3}, "fehler": None}
    assert a[1]["fehler"].startswith("Argumente kein gültiges JSON")
    assert a[2]["id"] == "aufruf_2" and a[2]["fehler"] == "Argumente sind kein JSON-Objekt"
    assert lc.werkzeug_aufrufe({"content": "fertig"}) == []


@pytest.mark.asyncio
async def test_anderer_modus_benannt():
    class K:
        api_mode = "anthropic_messages"
    with pytest.raises(lc.WerkzeugeNichtUnterstuetzt, match="anthropic_messages"):
        await lc.generate_with_tools(K(), [], [])
