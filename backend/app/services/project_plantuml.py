"""PlantUML export for projects — Werkbank artefact only, NOT used for UI rendering.

The UI uses Cytoscape.js (interactive). This module generates .puml source files
that the Werkbank writes to disk so Claude/Codex can read/understand the plan.
"""
from __future__ import annotations

import zlib
import base64
from typing import Any

# PlantUML uses a custom base64 alphabet
_PU_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
_STD_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_TRANS = str.maketrans(_STD_CHARS, _PU_CHARS)


def _encode(text: str) -> str:
    """Compress PlantUML source and encode with PlantUML's base64 variant."""
    compressed = zlib.compress(text.encode("utf-8"), 9)[2:-4]  # strip zlib header/trailer
    b64 = base64.b64encode(compressed).decode("ascii")
    return b64.translate(_TRANS)


_STATUS_COLORS = {
    "done":        "#90EE90",
    "in_progress": "#FFE680",
    "pending":     "#E0E0E0",
}
_CRITICAL_COLOR = "#FFCC99"  # Tanoi/Butterscotch — per design system, never pink


def _step_color(step: dict[str, Any]) -> str:
    if step.get("critical"):
        return _CRITICAL_COLOR
    return _STATUS_COLORS.get(step.get("status", "pending"), "#E0E0E0")


def _safe_label(text: str, max_len: int = 40) -> str:
    text = text.replace('"', "'").replace("\n", " ")
    if len(text) > max_len:
        text = text[:max_len - 1] + "…"
    return text


def render_network(
    project_name: str,
    steps: list[dict[str, Any]],
    deps: list[dict[str, Any]],
) -> str:
    """Generate a PlantUML dependency (PERT) diagram."""
    lines = [
        "@startuml",
        f"title {_safe_label(project_name, 60)}",
        "skinparam rectangle {",
        "  FontSize 11",
        "  RoundCorner 8",
        "}",
        "skinparam arrow {",
        "  Color #555555",
        "}",
        "left to right direction",
        "",
    ]

    id_to_alias: dict[str, str] = {}
    for i, step in enumerate(steps):
        sid = str(step["id"])
        alias = f"S{i}"
        id_to_alias[sid] = alias
        color = _step_color(step)
        dur = step.get("duration_days", 1)
        issue_type = step.get("jira_issue_type", "task").upper()
        jira_key = step.get("jira_key") or ""
        label_parts = [_safe_label(step["title"])]
        label_parts.append(f"[{issue_type}] {dur}d")
        if jira_key:
            label_parts.append(jira_key)
        label = "\\n".join(label_parts)
        lines.append(f'rectangle "{label}" as {alias} {color}')

    lines.append("")
    for dep in deps:
        a = id_to_alias.get(str(dep["depends_on_step_id"]))
        b = id_to_alias.get(str(dep["step_id"]))
        if a and b:
            lines.append(f"{a} --> {b}")

    lines.append("@enduml")
    return "\n".join(lines)


def render_gantt(
    project_name: str,
    steps: list[dict[str, Any]],
    deps: list[dict[str, Any]],
) -> str:
    """Generate a PlantUML @startgantt diagram."""
    lines = [
        "@startgantt",
        f"Project starts 2000-01-01",
        "printscale daily",
        f"title {_safe_label(project_name, 60)}",
        "",
    ]

    id_to_alias: dict[str, str] = {}
    has_es = all(s.get("est_start") is not None for s in steps)

    for i, step in enumerate(steps):
        sid = str(step["id"])
        alias = f"[{_safe_label(step['title'], 30)}]"
        id_to_alias[sid] = alias
        dur = step.get("duration_days", 1)
        status = step.get("status", "pending")

        if has_es:
            start_day = step.get("est_start", 0)
            lines.append(f"{alias} starts 2000-01-{1 + start_day:02d} and lasts {dur} days")
        else:
            lines.append(f"{alias} lasts {dur} days")

        if status == "done":
            lines.append(f"{alias} is 100% complete")
        elif status == "in_progress":
            lines.append(f"{alias} is 50% complete")

    # Add dependency links only if no CPM dates (otherwise dates already encode deps)
    if not has_es:
        lines.append("")
        for dep in deps:
            a = id_to_alias.get(str(dep["depends_on_step_id"]))
            b = id_to_alias.get(str(dep["step_id"]))
            if a and b:
                lines.append(f"{b} starts at {a}'s end")

    lines.append("@endgantt")
    return "\n".join(lines)


def render_markdown(
    project_name: str,
    description: str | None,
    steps: list[dict[str, Any]],
    deps: list[dict[str, Any]],
) -> str:
    """Human-readable plan document for the Werkbank."""
    id_to_title: dict[str, str] = {str(s["id"]): s["title"] for s in steps}

    lines = [f"# {project_name}", ""]
    if description:
        lines += [description, ""]

    lines += ["## Schritte", ""]
    for step in steps:
        sid = str(step["id"])
        status_icon = {"done": "✅", "in_progress": "🔄", "pending": "⬜"}.get(step.get("status", "pending"), "⬜")
        itype = step.get("jira_issue_type", "task").upper()
        jira = f" ([{step['jira_key']}])" if step.get("jira_key") else ""
        lines.append(f"### {status_icon} {step['title']}{jira} `{itype}` {step.get('duration_days',1)}d")
        if step.get("description"):
            lines.append("")
            lines.append(step["description"])
        dep_titles = [id_to_title[str(d["depends_on_step_id"])] for d in deps if str(d["step_id"]) == sid and str(d["depends_on_step_id"]) in id_to_title]
        if dep_titles:
            lines.append("")
            lines.append(f"*Abhängig von:* {', '.join(dep_titles)}")
        lines.append("")

    return "\n".join(lines)
