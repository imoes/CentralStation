"""Manpage-Werkzeuge: Aufbereitung, Fehlerarten, Inventar-Prüfung, Agenten-Grenze."""
from __future__ import annotations

import pytest

from app.services import manpages, probe_client
from app.services.manpages import condense, parse_apropos, parse_capabilities
from app.services.probe_client import ProbeError

RSYNC = """RSYNC(1)                        User Commands                       RSYNC(1)

NAME
       rsync - a fast, versatile, remote (and local) file-copying tool

SYNOPSIS
       Local:
           rsync [OPTION...] SRC... [DEST]

DESCRIPTION
       Rsync is a fast and extraordinarily versatile file copying tool.

OPTIONS
       --verbose, -v
              This option increases the amount of information you are given.

       --delete
              This tells rsync to delete extraneous files from the receiving side.

       --dry-run, -n
              This makes rsync perform a trial run that doesn't make any changes.

EXIT VALUES
       0      Success
"""


def test_apropos_lines_are_parsed():
    out = parse_apropos("rsync (1)            - a fast, versatile, remote file-copying tool\n"
                        "rsyncd.conf (5)      - configuration file for rsync in daemon mode\n"
                        "garbage line\n")
    assert out[0] == {"name": "rsync", "section": "1",
                      "summary": "a fast, versatile, remote file-copying tool"}
    assert len(out) == 2


def test_condense_with_focus_returns_exactly_that_option():
    c = condense(RSYNC, focus="--delete")
    assert c["focus_found"] is True
    assert "delete extraneous files" in c["text"]
    assert "trial run" not in c["text"]          # andere Optionen nicht
    assert "SYNOPSIS" in c["text"] and "ABSCHNITTE: NAME, SYNOPSIS" in c["text"]


def test_condense_without_focus_gives_description_and_sections():
    c = condense(RSYNC)
    assert "DESCRIPTION (Anfang)" in c["text"] and c["focus_found"] is None
    assert "EXIT VALUES" in c["text"]


def test_option_definition_beats_a_mere_mention():
    text = RSYNC.replace("DESCRIPTION\n       Rsync is", "DESCRIPTION\n       Careful with --delete, it removes files.\n       Rsync is")
    c = condense(text, focus="--delete")
    assert "delete extraneous files" in c["text"]
    assert "Careful with --delete" not in c["text"]


def test_alias_in_head_is_found():
    assert "trial run" in condense(RSYNC, focus="-n")["text"]


def test_word_focus_falls_back_to_the_paragraph():
    c = condense(RSYNC, focus="versatile")
    assert "extraordinarily versatile" in c["text"] and c["focus_found"]


def test_condense_reports_missing_focus():
    c = condense(RSYNC, focus="--frobnicate")
    assert c["focus_found"] is False and "DESCRIPTION (Anfang)" in c["text"]


def test_capabilities_are_parsed():
    text = ('NAME="Debian GNU/Linux"\nID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian 13"\n'
            "---uname\nLinux 6.12 x86_64\n---id\nuid=1001(probe) gid=1001(probe)\n"
            "---tools\nsystemctl /usr/bin/systemctl\ndocker /usr/bin/docker\n"
            "---sudo\n    (root) NOPASSWD: /usr/bin/docker ps\n")
    caps = parse_capabilities(text)
    assert caps["os"] == {"id": "debian", "version_id": "13", "pretty_name": "Debian 13"}
    assert caps["tools"]["docker"] == "/usr/bin/docker"
    assert caps["identity"].startswith("uid=1001") and caps["sudo"]


async def test_errors_stay_distinguishable(monkeypatch):
    async def boom(*a, **k):
        raise ProbeError("host_unknown", "nicht im Inventar")
    monkeypatch.setattr(manpages, "run_probe", boom)
    manpages._cache.clear()
    r = await manpages.read_manpage("x", "rsync", "", "", "u")
    assert r == {"ok": False, "error": "host_unknown", "message": "nicht im Inventar"}


async def test_empty_manpage_is_no_manpage_not_empty_success(monkeypatch):
    async def empty(*a, **k):
        return {"host": "web01", "stdout": "", "exit_code": 0, "error": None}
    monkeypatch.setattr(manpages, "run_probe", empty)
    manpages._cache.clear()
    r = await manpages.read_manpage("web01", "frob", "", "", "u")
    assert r["ok"] is False and r["error"] == "no_manpage"


async def test_results_are_cached(monkeypatch):
    calls = []

    async def fake(host, template, params, **k):
        calls.append(template)
        return {"host": host, "stdout": RSYNC, "exit_code": 0, "error": None}
    monkeypatch.setattr(manpages, "run_probe", fake)
    manpages._cache.clear()
    await manpages.read_manpage("web01", "rsync", "--delete", "", "u")
    r = await manpages.read_manpage("web01", "rsync", "--dry-run", "", "u")
    assert calls == ["man_read"] and r["cached"] and "trial run" in r["text"]


async def test_agents_cannot_use_system_templates():
    with pytest.raises(ProbeError) as exc:
        await probe_client.run_probe("web01", "journal_tail", {"unit": "nginx"}, agent=True)
    assert exc.value.kind == "refused"


async def test_hosts_outside_the_inventory_are_refused(monkeypatch):
    async def inv():
        return {"web01.example.org", "db01.example.org"}
    monkeypatch.setattr(probe_client, "_inventory_hosts", inv)
    assert await probe_client.resolve_host("WEB01") == "web01.example.org"
    assert await probe_client.resolve_host("db01.example.org") == "db01.example.org"
    with pytest.raises(ProbeError) as exc:
        await probe_client.resolve_host("evil.example.org")
    assert exc.value.kind == "host_unknown"
