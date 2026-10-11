"""Lese-Vorlagen (cs_policy): die einzigen Befehle, die ohne Freigabe auf Servern laufen.

Geprüft wird die Zusage "zu 100 % lesend": nur Vorlagen, Parameter nur nach Muster und
gequotet, unbekannte Vorlagen/Parameter sind Fehler — und kein Weg, freien Text
einzuschleusen.
"""
from __future__ import annotations

import os
import stat

import pytest

from cs_policy.sshexec import check_host, run_ssh, ssh_argv
from cs_policy.templates import TEMPLATES, PolicyError, agent_templates, render


def test_every_template_renders_with_its_defaults_or_sample():
    samples = {"query": "rsync", "page": "rsync", "unit": "nginx.service",
               "name": "web-1", "package": "openssh-server"}
    for tid, tpl in TEMPLATES.items():
        params = {n: samples[n] for n, p in tpl.params.items() if p.required and p.default is None}
        assert render(tid, params)


@pytest.mark.parametrize("tid,params", [
    ("man_read", {"page": "ls; rm -rf /"}),
    ("man_read", {"page": "ls", "section": "1; id"}),
    ("man_read", {"page": "$(id)"}),
    ("man_read", {"page": "-P sh"}),
    ("man_search", {"query": "a b"}),
    ("service_status", {"unit": "-H evil"}),
    ("service_status", {"unit": "nginx\nreboot"}),
    ("journal_tail", {"unit": "nginx", "lines": "5000"}),
    ("journal_tail", {"unit": "nginx", "since": "yesterday; id"}),
    ("container_logs", {"name": "web`id`"}),
    ("package_version", {"package": "../../etc/passwd"}),
])
def test_parameters_outside_their_pattern_are_refused(tid, params):
    with pytest.raises(PolicyError):
        render(tid, params)


def test_unknown_template_and_unknown_parameter_are_errors():
    with pytest.raises(PolicyError):
        render("rm_everything")
    with pytest.raises(PolicyError):
        render("man_read", {"page": "ls", "command": "id"})


def test_values_are_quoted_even_when_valid():
    assert render("man_read", {"page": "rsync"}).endswith("-- rsync 2>/dev/null | col -b")
    # shlex.quote lässt harmlose Namen unverändert und quotet alles andere.
    assert render("service_status", {"unit": "nginx@1.service"}).endswith("-- nginx@1.service")


def test_no_template_offers_help_flag_execution():
    """`x --help` führt Programme aus, die --help ignorieren — bewusst keine Vorlage."""
    assert not any("--help" in t.command for t in TEMPLATES.values())


def test_agents_only_get_documentation_and_capabilities():
    assert set(agent_templates()) == {"man_search", "man_read", "host_capabilities"}


def test_docker_format_survives_str_format():
    assert "{{.Names}}" in render("docker_ps")


@pytest.mark.parametrize("host", [
    "localhost", "host.docker.internal", "127.0.0.1", "::1", "169.254.1.1",
    "-oProxyCommand=sh", "user@web01", "web01;id", "", "a" * 300,
])
def test_bad_hosts_are_refused(host):
    with pytest.raises(PolicyError):
        check_host(host)


def test_good_hosts_are_normalised():
    assert check_host("Web01.Example.org.") == "web01.example.org"
    assert check_host("10.0.1.5") == "10.0.1.5"


def test_ssh_argv_never_lets_the_host_become_an_option():
    argv = ssh_argv("web01", "uptime")
    assert argv[argv.index("--") + 1:] == ["web01", "uptime"]
    assert "BatchMode=yes" in argv and "ClearAllForwardings=yes" in argv


def _fake_ssh(tmp_path, body: str) -> str:
    p = tmp_path / "ssh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_run_ssh_reports_output_and_exit(tmp_path):
    fake = _fake_ssh(tmp_path, 'echo "ran: $@" ; exit 3')
    r = run_ssh("web01", "uptime", ssh_binary=fake)
    assert r.exit_code == 3 and r.error is None and "web01 uptime" in r.stdout


def test_run_ssh_distinguishes_ssh_failure(tmp_path):
    fake = _fake_ssh(tmp_path, 'echo "Permission denied" >&2; exit 255')
    r = run_ssh("web01", "uptime", ssh_binary=fake)
    assert r.error == "ssh_failed" and not r.ok


def test_run_ssh_kills_on_timeout(tmp_path):
    fake = _fake_ssh(tmp_path, "sleep 30")
    r = run_ssh("web01", "uptime", ssh_binary=fake, timeout=0.5)
    assert r.timed_out and r.error == "timeout" and r.duration_ms < 5000


def test_run_ssh_caps_output(tmp_path):
    fake = _fake_ssh(tmp_path, "head -c 5000 /dev/zero")
    r = run_ssh("web01", "x", ssh_binary=fake, max_bytes=1000)
    assert r.truncated and len(r.stdout) == 1000
