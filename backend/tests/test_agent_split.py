"""Getrennter Agent-Nutzer (Block 5): welcher Modus gilt, und wer schreibt wohin."""
from __future__ import annotations

from app.services import userenv_manager as um


class _C:
    def __init__(self, split: bool):
        self.labels = {um.SPLIT_LABEL: "1" if split else "0"}
        self.calls = []

    def exec_run(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return (0, b"")


def test_split_users_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CS_AGENT_SPLIT_USERS", "a-1, b-2")
    assert um.split_wanted("a-1") and um.split_wanted("b-2") and not um.split_wanted("c-3")
    monkeypatch.setenv("CS_AGENT_SPLIT_USERS", "*")
    assert um.split_wanted("anyone")
    monkeypatch.delenv("CS_AGENT_SPLIT_USERS")
    assert not um.split_wanted("a-1")


def test_agent_exec_runs_as_agent_only_in_split_mode():
    c = _C(split=True)
    um._agent_exec(c, ["true"], environment={"X": "1"})
    cmd, kw = c.calls[0]
    assert kw["user"] == um.AGENT_UID
    assert kw["environment"]["HOME"] == "/home/agent" and kw["environment"]["X"] == "1"
    assert kw["environment"]["CLAUDE_CONFIG_DIR"] == "/home/agent/.claude"
    legacy = _C(split=False)
    um._agent_exec(legacy, ["true"])
    assert "user" not in legacy.calls[0][1]            # bisheriger Weg unverändert


def test_agent_home_follows_the_mode():
    assert um.agent_home(_C(True)) == "/home/agent"
    assert um.agent_home(_C(False)) == "/home/yolo"
    assert not um.is_split(object())                   # Container ohne Label = bisheriger Modus
