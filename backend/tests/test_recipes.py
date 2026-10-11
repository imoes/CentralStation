"""Rezepte: aus einer gelösten Sitzung lernen — nur Vorlagen werden ausführbar."""
from __future__ import annotations

import json

from app.services.error_clusters.checks import planned_steps
from app.services.recipes import (
    _calls_for_prompt, materialize, parse_answer, prompt_lines, scope_for, validate_recipe_steps,
)

HOSTS = ["vpp0199.example.example", "vpp0207.example.example"]


def test_answer_keeps_templates_and_moves_the_rest_to_text():
    raw = "<think>…</think>" + json.dumps({
        "titel": "Backup-Pool voll", "problem": "data/backup über 90 %", "loesung": "Alte Snapshots gelöscht",
        "schritte": [
            {"vorlage": "disk_usage", "parameter": {}, "host": None, "warum": "Belegung"},
            {"vorlage": "journal_tail", "parameter": {"unit": "pvescheduler.service"}, "warum": "Jobs"},
            {"vorlage": "shell", "parameter": {"cmd": "zfs list -t snapshot"}},          # keine Vorlage
            {"vorlage": "service_status", "parameter": {"unit": "x; reboot"}},           # ungültig
            {"vorlage": "disk_usage", "parameter": {}, "host": "fremd.example"},         # fremder Host
        ],
        "weitere_befehle": [{"befehl": "zfs destroy data/backup@old", "zweck": "Platz", "schreibend": True}],
    })
    out = parse_answer(raw, HOSTS)
    assert [s["template"] for s in out["steps"]] == ["disk_usage", "journal_tail", "disk_usage"]
    assert out["steps"][2]["host"] is None             # fremder Host → gilt für Betroffene
    assert out["other_commands"] == [{"command": "zfs destroy data/backup@old", "purpose": "Platz",
                                      "writes": True}]


def test_unusable_answers_are_none():
    assert parse_answer("", HOSTS) is None
    assert parse_answer("kein json", HOSTS) is None
    assert parse_answer('{"problem": "ohne Titel"}', HOSTS) is None


def test_materialize_targets_this_outbreaks_hosts():
    steps = validate_recipe_steps([{"vorlage": "memory", "parameter": {}},
                                   {"vorlage": "disk_usage", "parameter": {}, "host": "vpp0199"}], HOSTS)
    out = materialize(steps, ["a.x", "b.x", "c.x", "2c51e08bb9c9"])
    assert out == [{"host": "a.x", "template": "memory", "params": {}},
                   {"host": "b.x", "template": "memory", "params": {}},
                   {"host": "vpp0199", "template": "disk_usage", "params": {}}]


class _Rec:
    def __init__(self, steps):
        self.steps = steps


def test_recipe_steps_come_first_and_duplicates_drop():
    ctx = {"hosts": ["a.x"], "triage": {"pruefschritte_auto": [
        {"host": "a.x", "template": "memory", "params": {}},
        {"host": "a.x", "template": "docker_ps", "params": {}}]}}
    steps, origin = planned_steps(ctx, _Rec([{"template": "memory", "params": {}, "host": None}]))
    assert origin == "rezept+triage"
    assert [s["template"] for s in steps] == ["memory", "docker_ps"]
    assert planned_steps({"hosts": ["a.x"]}, _Rec([{"template": "disk_usage", "params": {}, "host": None}]))[1] == "rezept"


class _Inc:
    def __init__(self, host):
        self.primary_host = host


def test_scope_prefers_the_pattern():
    assert scope_for(_Inc("Web01.X"), "abc123") == ("template", "abc123")
    assert scope_for(_Inc("Web01.X"), None) == ("host", "web01.x")


def test_prompt_lines_flag_writing_commands():
    text = "\n".join(prompt_lines({"title": "T", "problem": "P", "solution": "L",
                                   "steps": [{"template": "memory", "params": {}, "host": None, "why": "w"}],
                                   "other_commands": [{"command": "systemctl restart x", "purpose": "fix",
                                                       "writes": True}]}))
    assert "Bewährtes Rezept" in text and "(betroffene Hosts)" in text
    assert "SCHREIBEND — nur mit Freigabe" in text
    assert prompt_lines(None) == []


def test_calls_for_prompt_keeps_commands_and_drops_noise():
    out = _calls_for_prompt([{"tool": "Bash", "command": "ssh h 'df -h'"},
                             {"tool": "ToolSearch", "input": {"query": "x"}},
                             {"tool": "mcp__centralstation__read_manpage", "input": {"command": "zfs"}}])
    assert out[0] == "Bash: ssh h 'df -h'" and len(out) == 2
