"""Werkzeug-Momentaufnahmen der Claude CLI vor dem Fortsetzen entfernen (userenv)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[2] / "userenv" / "claude_session.py"
pytestmark = pytest.mark.skipif(not _MOD.is_file(), reason="userenv/ fehlt im Backend-Image")


def _load():
    spec = importlib.util.spec_from_file_location("claude_session", _MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cs = _load() if _MOD.is_file() else None

SNAP = json.dumps({"type": "attachment", "attachment": {"type": "deferred_tools_record",
                   "entries": [{"name": "mcp__centralstation__create_jira_ticket"}]}}) + "\n"
USER = json.dumps({"type": "user", "message": {"content": "erwähnt deferred_tools_record nur"}}) + "\n"
ASSIST = json.dumps({"type": "assistant", "message": {"content": "ok"}}) + "\n"


def test_only_tool_snapshots_are_dropped():
    kept, n = cs.drop_tool_snapshots([USER, SNAP, ASSIST, SNAP])
    assert n == 2
    assert kept == [USER, ASSIST]          # Verlauf bleibt vollständig, auch wenn er das Wort nennt


def test_refresh_rewrites_the_session_file(tmp_path):
    proj = tmp_path / "projects" / "-home-yolo-workspaces"
    proj.mkdir(parents=True)
    f = proj / "sid-1.jsonl"
    f.write_text(USER + SNAP + ASSIST, encoding="utf-8")
    assert cs.refresh_tool_snapshots(str(tmp_path), "sid-1") == 1
    assert f.read_text(encoding="utf-8") == USER + ASSIST
    assert cs.refresh_tool_snapshots(str(tmp_path), "sid-1") == 0     # nichts mehr zu tun


def test_missing_session_is_not_an_error(tmp_path):
    assert cs.refresh_tool_snapshots(str(tmp_path), "gibt-es-nicht") == 0
