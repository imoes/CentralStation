"""Claude-CLI-Sitzungen: veraltete Werkzeug-Momentaufnahmen vor dem Fortsetzen entfernen.

Die Claude CLI legt im Sitzungsprotokoll (~/.claude/projects/<cwd>/<sid>.jsonl) Einträge
`{"type": "attachment", "attachment": {"type": "deferred_tools_record", …}}` ab — eine
Momentaufnahme aller MCP-Werkzeuge mit Beschreibung und Parametern. Beim `--resume`
nimmt sie DIESE Aufnahme statt der aktuellen Werkzeugliste. Eine geänderte
Werkzeugdefinition erreichte eine laufende Sitzung deshalb nie (gemessen 2026-10-02:
create_jira_ticket mit 7 Parametern, die fortgesetzte Sitzung sah weiter 3 — auch ein
Container-Neustart hätte daran nichts geändert).

Entfernt die CLI-Aufnahme, baut die CLI sie beim Start aus der Live-Liste neu. Bei
unveränderten Werkzeugen ist der Inhalt identisch, der Prompt-Cache bleibt also gültig.
"""
from __future__ import annotations

import glob
import json
import os

RECORD_TYPE = "deferred_tools_record"


def is_tool_snapshot(line: str) -> bool:
    """True für eine Zeile mit einer Werkzeug-Momentaufnahme (nicht zum Verlauf gehörig)."""
    if RECORD_TYPE not in line:          # schneller Vorfilter, ohne JSON zu parsen
        return False
    try:
        d = json.loads(line)
    except ValueError:
        return False
    return d.get("type") == "attachment" and (d.get("attachment") or {}).get("type") == RECORD_TYPE


def drop_tool_snapshots(lines: list[str]) -> tuple[list[str], int]:
    """Alle Zeilen ohne Momentaufnahmen, und wie viele entfernt wurden."""
    kept = [l for l in lines if not is_tool_snapshot(l)]
    return kept, len(lines) - len(kept)


def refresh_tool_snapshots(config_dir: str, session_id: str) -> int:
    """Momentaufnahmen aus der Sitzungsdatei entfernen (atomar). Gibt die Anzahl zurück.

    Fehler sind nicht fatal: dann setzt die CLI eben mit der alten Aufnahme fort, wie
    bisher — die Antwort soll daran nicht scheitern.
    """
    removed = 0
    for path in glob.glob(os.path.join(config_dir, "projects", "*", f"{session_id}.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            kept, n = drop_tool_snapshots(lines)
            if not n:
                continue
            tmp = path + ".cs-tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
            os.replace(tmp, path)
            removed += n
        except OSError:
            continue
    return removed
