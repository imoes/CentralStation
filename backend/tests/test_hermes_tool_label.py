"""Anzeigename der Werkzeugschritte in der Konsole (userenv/main.py, hermes_tool_label).

Anlass 25.10.2026: ein gieriger Ausdruck machte aus query_idgen_sql "sql" — die Konsole
zeigte "sql: SELECT …" statt des Werkzeugs, ebenso "host", "base" usw.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[2] / "userenv" / "main.py"
pytestmark = pytest.mark.skipif(not _MAIN.is_file(), reason=f"{_MAIN} fehlt (Backend-Image)")


def _label():
    mod = ast.parse(_MAIN.read_text())
    fn = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "hermes_tool_label")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(_MAIN), "exec"), ns)
    return ns["hermes_tool_label"]


@pytest.mark.parametrize("name,expected", [
    ("mcp__centralstation__query_idgen_sql", "query idgen sql"),
    ("mcp__centralstation__lookup_idgen_host", "lookup idgen host"),
    ("mcp__centralstation__search_knowledge_base", "search knowledge base"),
    ("mcp__vibemk__vibemk_get_host_status", "vibemk get host status"),
    ("terminal", "terminal"),
    ("web_search", "web search"),
])
def test_only_the_server_prefix_is_removed(name, expected):
    assert _label()(name) == expected


def test_progress_callback_uses_the_module_function():
    assert "_tool_label = hermes_tool_label" in _MAIN.read_text()
