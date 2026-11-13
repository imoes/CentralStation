"""Hermes in der Konsole findet die VI (userenv/main.py) — Anlass 13.11.2026, Admin-Konto.

Die MCP-Vorabsuche beim Containerstart las config.yaml, bevor das Backend den Server „vi“ eintrug; sie lief nur
einmal, jede Sitzung startete mit „Unknown toolset: mcp-vi“. Zusätzlich nannte der Systemprompt die alten deutschen
Werkzeugnamen mit falschem Präfix (mcp_vi_vi_erinnere); vi-mcp listet nur englische (vi_recall …), Hermes benennt
MCP-Werkzeuge mcp__<server>__<werkzeug>.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[2] / "userenv" / "main.py"
pytestmark = pytest.mark.skipif(not _MAIN.is_file(), reason=f"{_MAIN} fehlt (Backend-Image)")


def _fn(name: str):
    mod = ast.parse(_MAIN.read_text())
    fn = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(_MAIN), "exec"), ns)
    return ns[name]


def test_fehlende_server_werden_erkannt():
    f = _fn("_fehlende_mcp_server")
    alle = ["mcp-centralstation", "mcp-playwright", "mcp-vi", "mcp-vibemk"]
    assert f(alle, {"centralstation", "playwright", "vibemk"}) == ["vi"]
    assert f(alle, {"centralstation", "playwright", "vi", "vibemk"}) == []


def test_jede_sitzung_laedt_fehlende_server_nach():
    src = _MAIN.read_text()
    teil = src[src.index("def _make_agent"):src.index("agent = AIAgent(")]
    assert "_mcp_nachladen(_mcp_toolsets)" in teil


def test_systemprompt_nennt_die_vi_werkzeuge_wie_hermes_sie_registriert():
    src = _MAIN.read_text()
    assert "mcp__vi__vi_recall" in src and "mcp__vi__vi_learn" in src
    for alt in ("mcp_vi_vi_", "vi_erinnere", "vi_lerne", "vi_vertiefe", "vi_widerlege", "vi_bestaetige"):
        assert alt not in src, alt


def test_firmennetz_als_rueckfall_mit_richtigem_tag():
    """13.11.: interne Doku (12 994 Confluence-Objekte) liegt nur in der Firmen-VI — Netz „firma“, live über
    vi_net_search; ein Agent suchte „quelle:confluence“ statt „quelle_art:confluence“."""
    src = _MAIN.read_text()
    assert "vi_net_search(network='firma'" in src and "quelle_art:confluence" in src
