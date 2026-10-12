"""Tickets suchen: Freitext → JQL, Schlüssel direkt, Textstelle als Begründung."""
from __future__ import annotations

import pytest

from app.services.ticketing import build_search_jql, snippet


def test_free_text_becomes_text_search():
    assert build_search_jql("zipline 401") == 'text ~ "zipline 401" ORDER BY updated DESC'


def test_issue_key_is_searched_directly():
    assert build_search_jql("imit-1234") == "issuekey = IMIT-1234 ORDER BY updated DESC"


def test_quotes_and_backslashes_cannot_break_out_of_the_jql_string():
    jql = build_search_jql('a" OR project = X OR text ~ "b\\')
    assert jql.startswith('text ~ "a\\" OR project = X OR text ~ \\"b\\\\"')


def test_project_and_open_filter():
    assert build_search_jql("backup", "imit", True) == \
        'text ~ "backup" AND project = "IMIT" AND statusCategory != Done ORDER BY updated DESC'


def test_nothing_to_search_is_an_error():
    with pytest.raises(ValueError):
        build_search_jql("")


def test_snippet_shows_why_it_matched():
    text = "Lange Einleitung " * 10 + "Der Zipline-Dienst liefert 401 seit dem Update." + " Rest" * 20
    s = snippet(text, "zipline 401")
    assert "Zipline-Dienst liefert 401" in s and s.startswith("…")
    assert snippet(None, "x") == ""
    assert snippet("kurz", "nichts") == "kurz"
