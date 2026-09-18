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


def _load():
    spec = importlib.util.spec_from_file_location("cs_readonly_guard", _GUARD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


guard = _load()


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
