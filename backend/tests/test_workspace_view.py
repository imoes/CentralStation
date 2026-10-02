"""Welche Workspace-Dateien die Konsole anzeigt (Berichte des Agenten)."""
from __future__ import annotations

import pytest

from app.services.workspace_view import WORKSPACE_VIEW_MAX_BYTES, workspace_view_kind  # noqa: E402


@pytest.mark.parametrize("path,kind", [
    ("/home/yolo/workspaces/reports/KTLNXTM-70.md", "markdown"),
    ("reports/x.MARKDOWN", "markdown"),
    ("logs/run.log", "text"),
    ("data/export.csv", "text"),
])
def test_text_files_are_shown(path, kind):
    assert workspace_view_kind(path, 1000) == (kind, "")


def test_binary_and_unknown_types_point_to_the_werkbank():
    kind, reason = workspace_view_kind("report.xlsx", 1000)
    assert kind is None and "Werkbank" in reason


def test_size_limit_names_the_reason():
    kind, reason = workspace_view_kind("big.md", WORKSPACE_VIEW_MAX_BYTES + 1)
    assert kind is None and "zu groß" in reason
