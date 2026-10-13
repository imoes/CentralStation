"""Ticket aus einem Incident (13.10.2026): KI-Entwurf aus dem Lagebild, Rückverweis, Vermerk."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

from app.services.ticketing import incident_link_appended, incident_tickets

URL = "https://hal.example/feed?incident=0c3f"
API = Path(__file__).parents[1] / "app/api"


def test_draft_and_create_accept_an_incident():
    # app.api.* lässt sich in Tests nicht importieren (Modell-Stubs) — Quelltext prüfen.
    src = (API / "tickets.py").read_text()
    assert src.count("incident_id: str | None = None") == 2        # DraftBody + CreateBody
    assert '"type": "ticket", "key": jira_key' in src              # Vermerk am Incident
    assert "incident_link_appended(" in src                        # Rückverweis vom Backend


def test_incident_link_is_appended_once():
    d = incident_link_appended("h3. Problem\nOOM", URL)
    assert d.endswith(f"Vorfall in CentralStation: [{URL}]")
    assert incident_link_appended(d, URL) == d                 # nicht doppelt
    assert incident_link_appended("", URL).startswith("----")  # auch bei leerem Text


def test_incident_tickets_are_read_from_causal_context():
    inc = NS(causal_context=[
        {"type": "error_cluster", "template": "x"},
        {"type": "ticket", "key": "IMIT-1", "url": "u1"},
        {"incident_id": "abc"},                                    # Ursache ohne type
        {"type": "ticket", "key": "IMIT-2", "url": None},
    ])
    assert [t["key"] for t in incident_tickets(inc)] == ["IMIT-1", "IMIT-2"]
    assert incident_tickets(NS(causal_context=None)) == []


def test_ticket_prompt_forbids_tooling_and_ai_mentions():
    """Stilregel (Memory ticket_comment_style): Tickets lesen Menschen, oft Externe."""
    src = (Path(__file__).parents[1] / "app/services/ticketing.py").read_text()
    for word in ("NEVER mention how information", "confidence values", "'KI-Einschätzung'"):
        assert word in src


def test_console_handoff_and_ticket_share_one_briefing():
    """Ein Lagebild, zwei Verwender — sonst stünde im Ticket etwas anderes als in der Übergabe."""
    feed = (Path(__file__).parents[1] / "app/api/feed.py").read_text()
    tickets = (Path(__file__).parents[1] / "app/api/tickets.py").read_text()
    assert "lines = await incident_briefing(db, inc)" in feed
    assert "lines = await incident_briefing(db, inc)" in tickets
    assert feed.count("async def incident_briefing") == 1
