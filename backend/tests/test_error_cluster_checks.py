"""Prüfschritte (experimentell, nur per Knopf, nur lesend).

Die KI darf Schritte vorschlagen; ausgeführt wird nur, was eine cs_policy-Vorlage ist
und einen Host DIESES Ausbruchs betrifft.
"""
from __future__ import annotations

import json

from app.services.error_clusters.checks import planned_steps, prompt_lines
from app.services.error_clusters.triage import parse, validate_steps

HOSTS = ["cueservices.cue.example.example", "cue-zipline-1.cue.example.example"]


def test_only_templates_on_own_hosts_survive():
    steps = validate_steps([
        {"host": "cueservices", "vorlage": "docker_ps", "parameter": {}},                # ok
        {"host": "cueservices.cue.example.example", "vorlage": "container_logs",
         "parameter": {"name": "zipline-1", "lines": "200"}},                          # ok
        {"host": "evil.example", "vorlage": "disk_usage", "parameter": {}},             # fremder Host
        {"host": "cueservices", "vorlage": "rm_rf", "parameter": {}},                   # keine Vorlage
        {"host": "cueservices", "vorlage": "service_status", "parameter": {"unit": "x; reboot"}},
        {"host": "cueservices", "vorlage": "disk_usage", "parameter": {"extra": "1"}},  # fremder Parameter
        "systemctl restart docker",                                                     # Freitext
    ], HOSTS)
    assert [(s["host"], s["template"]) for s in steps] == [
        ("cueservices", "docker_ps"), ("cueservices.cue.example.example", "container_logs")]


def test_at_most_six_steps():
    many = [{"host": "cueservices", "vorlage": "disk_usage", "parameter": {}}] * 20
    assert len(validate_steps(many, HOSTS)) == 6


def test_triage_answer_carries_validated_steps():
    raw = json.dumps({"titel": "T", "zusammenfassung": "Z", "pruefschritte": ["Logs ansehen"],
                      "pruefschritte_auto": [
                          {"host": "cueservices", "vorlage": "docker_ps", "parameter": {}},
                          {"host": "cueservices", "vorlage": "shell", "parameter": {"cmd": "id"}}]})
    out = parse(raw, HOSTS)
    assert out["pruefschritte"] == ["Logs ansehen"]
    assert out["pruefschritte_auto"] == [{"host": "cueservices", "template": "docker_ps", "params": {}}]


def test_planned_steps_prefer_triage_then_basis():
    ctx = {"triage": {"pruefschritte_auto": [{"host": "a.x", "template": "docker_ps", "params": {}}]},
           "hosts": ["a.x"]}
    assert planned_steps(ctx) == (ctx["triage"]["pruefschritte_auto"], "triage")
    steps, origin = planned_steps({"docker_hosts": ["a.x", "b.x", "c.x", "d.x"], "hosts": []})
    assert origin == "basis" and len(steps) == 6
    assert {s["template"] for s in steps} == {"disk_usage", "memory"}
    assert planned_steps({"hosts": ["2c51e08bb9c9"]}) == ([], "basis")   # Container-ID ist kein Host


class _Inc:
    def __init__(self, ctx):
        self.causal_context = ctx


def test_prompt_lines_show_last_run_with_origin():
    inc = _Inc([{"type": "check_run", "at": "2026-10-11T13:00:00+00:00", "by": "a@x", "origin": "basis",
                 "results": [{"host": "a.x", "template": "disk_usage", "params": {}, "exit_code": 0,
                              "output": "/dev/sda1 90%"},
                             {"host": "b.x", "template": "memory", "params": {}, "error": "ssh_failed",
                              "message": ""}]}])
    text = "\n".join(prompt_lines(inc))
    assert "nur lesend" in text and "/dev/sda1 90%" in text and "FEHLER ssh_failed" in text
    assert prompt_lines(_Inc([])) == []
