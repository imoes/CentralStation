"""Policy tests for the Console's PreToolUse guard (userenv/cs-readonly-guard.py).

The guard decides, without a human in the loop, whether the Console agent may run a
shell command. Both directions of a mistake are expensive: too strict and the agent
cannot do its job, too loose and it changes a production system unasked. The rules
are regular expressions, which are easy to widen by accident — so the decisions are
pinned here rather than re-derived by reading them.

The file under test is not an installed module (it ships into the container image),
so it is loaded by path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parents[2] / "userenv" / "cs-readonly-guard.py"

# The guard ships into the Console image, not into the backend image, so inside the
# backend container there is no userenv/ directory. Skip rather than fail there: a
# red suite that means "wrong directory" trains people to ignore red suites. On a
# repo checkout — where this test is meant to run — the file is present.
pytestmark = pytest.mark.skipif(
    not _GUARD.is_file(),
    reason=f"guard not present at {_GUARD} (backend image has no userenv/ directory)",
)


def _load():
    spec = importlib.util.spec_from_file_location("cs_readonly_guard", _GUARD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


guard = _load() if _GUARD.is_file() else None


#: Commands the agent must be able to run unasked — diagnosis and its own sandbox.
ALLOWED = [
    # Read-only diagnosis, locally and over ssh
    "df -h",
    "cat /etc/os-release",
    "systemctl status nginx",
    "docker ps",
    "journalctl -u sshd --since '-1h'",
    "ssh cue0110 'df -h'",
    # Pulling a file FROM a host is reading it
    "scp cue0110:/var/log/messages .",
    "rsync -a cue0110:/home/marvin/out/ ./out/",
    # Writing into the agent's own workspace
    "echo hi > /home/yolo/workspaces/report.txt",
    # Python packages into the agent's own venv (/home/yolo/pip/venv)
    "pip install pandas",
    "pip3 install requests==2.31.0",
    "python3 -m pip install lxml",
    "uv pip install polars",
    "pip install -r requirements.txt",
    "pip uninstall -y pandas",
    "pip download numpy",
    "pip list",
]

#: Commands that must be refused until the user opens a write window.
BLOCKED = [
    # System-level operations, wherever they appear
    "systemctl restart nginx",
    "ssh cue0110 'systemctl restart nginx'",
    "apt-get install python3-pandas",
    "reboot",
    "docker restart cs-userenv-x",
    "git push origin main",
    # File writes against system paths or remote hosts
    "echo hi > /etc/motd",
    "rm -rf /var/log/foo",
    "ssh cue0110 'rm /home/marvin/x'",
    "scp a.txt cue0110:/home/marvin/",
    "scp -r ./scripts marvin@cue0110:/home/marvin/scripts",
    "rsync -av ./out/ cue0110:/home/marvin/out/",
    # Package managers other than pip stay blocked outright
    "npm install left-pad",
    "cargo install ripgrep",
    # pip, but not in the agent's own venv
    "sudo pip install pandas",
    "ssh cue0110 'pip install pandas'",
    "sshpass -p secret ssh host 'pip3 install foo'",
    "/usr/bin/pip install pandas",
    "pip install --break-system-packages pandas",
    "pip install --target /usr/lib/python3/dist-packages foo",
    # A permitted pip install must not launder the rest of a compound command
    "pip install pandas && rm -rf /etc/foo",
    "pip install pandas; systemctl restart nginx",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd: str) -> None:
    assert guard._is_write(cmd) is False, f"unexpectedly blocked: {cmd}"


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked(cmd: str) -> None:
    assert guard._is_write(cmd) is True, f"unexpectedly allowed: {cmd}"


def test_approval_file_is_root_owned_path() -> None:
    """Consent must live where the agent cannot write it.

    The agent runs as `yolo`; /opt is root-owned. If this path ever moved into the
    agent's own home, it could grant itself write access and the whole mechanism
    would be decorative.
    """
    assert guard.APPROVAL_FILE.startswith("/opt/")


# ── Claude's file tools (Write/Edit/…) ───────────────────────────────────────
# They write without a shell, so the Bash rules above never see them. The line is
# the same one the Bash rules draw: the workspace and /tmp are the agent's own,
# everything else — its own instructions, ssh config, system paths — is a change.

@pytest.mark.parametrize("path", [
    "/home/yolo/workspaces/reports/KTLNXTM-70_Migration.md",   # der gemeldete Fall
    "/home/yolo/workspaces/skript.py",
    "/tmp/notiz.txt",
])
def test_file_tool_inside_workspace_is_free(path: str) -> None:
    assert guard._file_write_outside(path) is False, path


@pytest.mark.parametrize("path", [
    "/home/yolo/.claude/CLAUDE.md",          # die eigene Anleitung umschreiben
    "/home/yolo/.claude/managed-settings.json",  # den eigenen Hook abschalten
    "/home/yolo/.ssh/config",
    "/etc/hosts",
    "/home/yolo/workspaces/../.bashrc",      # .. aus dem Workspace heraus
    "/home/yolo/workspaces-evil/x",          # Präfix ist kein Verzeichnis
    "",                                      # ohne Ziel wird nicht durchgewinkt
])
def test_file_tool_outside_workspace_needs_approval(path: str) -> None:
    assert guard._file_write_outside(path) is True, path


def test_file_tool_symlink_out_of_workspace_is_caught(tmp_path, monkeypatch) -> None:
    """Ein Symlink im Workspace, der nach draußen zeigt, zählt als draußen."""
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "link").symlink_to(outside)
    monkeypatch.setattr(guard, "FREE_WRITE_ROOTS", (str(ws),))
    assert guard._file_write_outside(str(ws / "ok.md")) is False
    assert guard._file_write_outside(str(ws / "link" / "evil.md")) is True


def test_relative_path_resolves_against_cwd() -> None:
    assert guard._file_write_outside("reports/x.md", "/home/yolo/workspaces") is False
    assert guard._file_write_outside("x.md", "/home/yolo/.claude") is True


def test_every_file_tool_is_in_the_hook_matcher() -> None:
    """Ein Datei-Werkzeug, das der Hook prüft, aber der Matcher nicht zu ihm schickt,
    ist ungeschützt — der Matcher steht in claude-managed-settings.json, die Liste hier."""
    import json
    import re
    cfg = json.loads((_GUARD.parent / "claude-managed-settings.json").read_text())
    (entry,) = cfg["hooks"]["PreToolUse"]
    assert entry["hooks"][0]["command"] == "python3 /opt/cs-readonly-guard.py"
    for tool in ("Bash", *guard.FILE_TOOLS):
        assert re.fullmatch(entry["matcher"], tool), tool


def test_managed_settings_land_where_claude_reads_them() -> None:
    """Claude Code liest verwaltete Einstellungen unter Linux nur aus /etc/claude-code/.
    Der alte Ort ~/.claude/managed-settings.json wurde nie gelesen — der Hook hing
    unbemerkt an einer Nutzerdatei auf einem Volume, das der Agent selbst beschreibt."""
    dockerfile = (_GUARD.parent / "Dockerfile").read_text()
    assert ("COPY userenv/claude-managed-settings.json "
            "/etc/claude-code/managed-settings.json") in dockerfile
