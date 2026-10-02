"""Welche Workspace-Dateien die Konsole anzeigt — reine Regel, für sich testbar.

Der Agent schreibt Berichte nach /home/yolo/workspaces/…; die Konsole verlinkt sie und
öffnet sie in einem neuen Fenster (GET /api/computer/workspace-file). Angezeigt werden
Textformate bis 2 MB; alles andere gehört in die Werkbank, mit genanntem Grund.
"""
from __future__ import annotations

import os

#: Was die Konsole aus dem Workspace anzeigt: Berichte und andere Textdateien, die der
#: Agent schreibt. Binärdateien gehen über die Werkbank.
WORKSPACE_VIEW_TYPES = {
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text", ".log": "text", ".csv": "text", ".tsv": "text",
    ".json": "text", ".yaml": "text", ".yml": "text", ".xml": "text",
    ".html": "text", ".sh": "text", ".py": "text", ".sql": "text", ".ini": "text",
    ".conf": "text", ".toml": "text",
}
WORKSPACE_VIEW_MAX_BYTES = 2 * 1024 * 1024


def workspace_view_kind(path: str, size: int) -> tuple[str | None, str]:
    """Darf die Konsole diese Datei anzeigen? (Art, Grund bei Nein)."""
    ext = os.path.splitext(path)[1].lower()
    kind = WORKSPACE_VIEW_TYPES.get(ext)
    if kind is None:
        return None, f"Dateityp {ext or '(ohne Endung)'} wird in der Konsole nicht angezeigt — in der Werkbank öffnen."
    if size > WORKSPACE_VIEW_MAX_BYTES:
        return None, (f"Datei zu groß für die Anzeige ({size // 1024} KB, erlaubt "
                      f"{WORKSPACE_VIEW_MAX_BYTES // (1024 * 1024)} MB) — in der Werkbank öffnen.")
    return kind, ""
