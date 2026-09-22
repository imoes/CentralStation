"""Path handling for `jira_add_attachment`.

The MCP server runs in the backend, which has far more of the host mounted than the
agent's own container does. The agent names a path and the backend opens it, so this
resolution is the only thing standing between "attach my report" and "attach
/etc/shadow". The rules are pinned here rather than left to review.
"""
from __future__ import annotations

import os

import pytest

from app.api.mcp_server import _resolve_workspace_file

USER = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A workspace laid out like the real one, plus a secret outside it."""
    base = tmp_path / "ide-workspaces"
    ws = base / USER / "workspaces"
    ws.mkdir(parents=True)
    (ws / "report.csv").write_text("a,b\n1,2\n")
    (ws / "sub").mkdir()
    (ws / "sub" / "log.txt").write_text("hello\n")

    secret = tmp_path / "secret.txt"
    secret.write_text("do not upload\n")

    monkeypatch.setenv("IDE_WORKSPACES_BASE", str(base))
    return {"ws": ws, "secret": secret}


def test_accepts_agent_absolute_path(workspace):
    full, err = _resolve_workspace_file(USER, "/home/yolo/workspaces/report.csv")
    assert err == ""
    assert full == os.path.realpath(str(workspace["ws"] / "report.csv"))


def test_accepts_relative_path(workspace):
    full, err = _resolve_workspace_file(USER, "sub/log.txt")
    assert err == ""
    assert full.endswith(os.path.join("sub", "log.txt"))


def test_rejects_missing_file(workspace):
    full, err = _resolve_workspace_file(USER, "nope.csv")
    assert full is None
    assert "nicht gefunden" in err


def test_rejects_empty_path(workspace):
    full, err = _resolve_workspace_file(USER, "   ")
    assert full is None
    assert err


def test_rejects_traversal(workspace):
    """`..` must not walk out of the workspace."""
    full, err = _resolve_workspace_file(USER, "../../../etc/passwd")
    assert full is None
    assert "heraus" in err


def test_rejects_foreign_absolute_path(workspace):
    """An absolute path that is not the agent's workspace is refused outright."""
    full, err = _resolve_workspace_file(USER, "/etc/passwd")
    assert full is None
    assert "Arbeitsverzeichnis" in err


def test_rejects_symlink_escape(workspace):
    """A symlink pointing outside is the case a string check on '..' would miss."""
    link = workspace["ws"] / "sneaky.txt"
    link.symlink_to(workspace["secret"])
    full, err = _resolve_workspace_file(USER, "sneaky.txt")
    assert full is None, "symlink out of the workspace was accepted"
    assert "heraus" in err


def test_user_cannot_reach_another_users_workspace(workspace, tmp_path):
    """The user id comes from the request; it must scope what can be read."""
    other = tmp_path / "ide-workspaces" / "99999999-0000-0000-0000-000000000000" / "workspaces"
    other.mkdir(parents=True)
    (other / "theirs.txt").write_text("private\n")

    full, err = _resolve_workspace_file(
        USER, "../../99999999-0000-0000-0000-000000000000/workspaces/theirs.txt"
    )
    assert full is None
    assert "heraus" in err


# ── Bild-Erkennung des Konsolen-Uploads ──────────────────────────────────────
#
# Der Content-Type des Browsers wird bewusst ignoriert — er ist Clientdaten. Was
# gespeichert wird, entscheiden die Magic Bytes, und daraus kommt auch die Endung.

from app.services.image_sniff import sniff_image as _sniff_image  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
JPEG = b"\xff\xd8\xff\xe0" + b"rest"
GIF = b"GIF89a" + b"rest"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"rest"


@pytest.mark.parametrize(
    "data,mime,ext",
    [
        (PNG, "image/png", ".png"),
        (JPEG, "image/jpeg", ".jpg"),
        (GIF, "image/gif", ".gif"),
        (WEBP, "image/webp", ".webp"),
    ],
)
def test_sniff_accepts_real_images(data, mime, ext):
    assert _sniff_image(data) == (mime, ext)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"nur text",
        b"<?php system($_GET[0]); ?>",
        b"\x7fELF\x02\x01\x01",              # eine Binärdatei, aber kein Bild
        b"RIFF\x00\x00\x00\x00WAVE",         # RIFF, aber WAVE statt WEBP
        b"GIF87",                            # abgeschnittene Signatur
    ],
)
def test_sniff_rejects_non_images(data):
    assert _sniff_image(data) is None, "Nicht-Bild wurde als Bild akzeptiert"


def test_sniff_ignores_the_declared_name():
    """Eine als .png benannte Textdatei ist kein Bild — der Name zählt hier nie."""
    assert _sniff_image(b"das ist in Wahrheit Text") is None
