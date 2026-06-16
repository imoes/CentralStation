"""Parser und Validator für den cs-meta-Block in CentralStation-Ansible-Playbooks.

Konvention: Jedes Playbook beginnt mit einem kommentierten YAML-Block:

    # ─── cs-meta ───────────────────────────────────────────────
    # id: disk-resize
    # title: Disk vergrößern (LVM)
    # description: Vergrößert ein LVM Logical Volume und das Dateisystem online
    # matches: ["checkmk:Filesystem*", "no space left on device"]
    # target: linux
    # risk: medium
    # params:
    #   - {name: lv_path,  example: "/dev/vg0/root"}
    # ─── /cs-meta ──────────────────────────────────────────────

ansible-playbook ignoriert die Kommentarzeilen. CentralStation strippt "# " und
parst den Block mit yaml.safe_load — kein Regex, voll strukturiert.
"""
from __future__ import annotations

import yaml

_OPEN  = "# ─── cs-meta"
_CLOSE = "# ─── /cs-meta"

REQUIRED_FIELDS = {"id", "title", "description", "matches", "target", "risk"}
VALID_TARGETS   = {"linux", "windows", "network", "generic"}
VALID_RISKS     = {"low", "medium", "high"}


def parse_meta(text: str) -> dict | None:
    """Extrahiert den cs-meta-Block aus einem Playbook-Text.

    Gibt None zurück wenn kein Block vorhanden oder das YAML ungültig ist.
    """
    start = text.find(_OPEN)
    end   = text.find(_CLOSE)
    if start == -1 or end == -1 or end <= start:
        return None

    block = text[start + len(_OPEN) : end]
    lines: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            lines.append(stripped[2:])
        elif stripped == "#":
            lines.append("")

    try:
        result = yaml.safe_load("\n".join(lines))
        return result if isinstance(result, dict) else None
    except yaml.YAMLError:
        return None


def validate_meta(meta: dict) -> list[str]:
    """Gibt eine Liste von Validierungsfehlern zurück. Leere Liste = valide."""
    errors: list[str] = []

    for field in sorted(REQUIRED_FIELDS):
        if not meta.get(field):
            errors.append(f"Pflichtfeld fehlt oder leer: {field}")

    matches = meta.get("matches")
    if matches is not None and not isinstance(matches, list):
        errors.append("matches muss eine YAML-Liste sein")
    elif isinstance(matches, list) and not matches:
        errors.append("matches darf nicht leer sein")

    risk = meta.get("risk", "")
    if risk and risk not in VALID_RISKS:
        errors.append(f"risk muss {' | '.join(sorted(VALID_RISKS))} sein, nicht '{risk}'")

    target = meta.get("target", "")
    if target and target not in VALID_TARGETS:
        errors.append(f"target muss {' | '.join(sorted(VALID_TARGETS))} sein, nicht '{target}'")

    params = meta.get("params")
    if params is not None:
        if not isinstance(params, list):
            errors.append("params muss eine YAML-Liste sein")
        else:
            for i, p in enumerate(params):
                if not isinstance(p, dict) or "name" not in p:
                    errors.append(f"params[{i}] braucht mindestens ein 'name'-Feld")

    return errors


def meta_to_awx_description(meta: dict) -> str:
    """Erzeugt einen reichhaltigen description-String für AWX Job Templates.

    AWX description wird vom remediation_matcher gelesen — je mehr Info,
    desto besser das LLM-Matching. Das matches-Feld liefert zusätzlich
    deterministisch auswertbare Muster.
    """
    parts = [meta.get("description", "")]
    matches = meta.get("matches")
    if matches:
        parts.append("Matches: " + ", ".join(matches))
    target = meta.get("target")
    if target:
        parts.append(f"Target: {target}")
    risk = meta.get("risk")
    if risk:
        parts.append(f"Risk: {risk}")
    return "\n".join(filter(None, parts))


def meta_to_survey_spec(meta: dict) -> dict | None:
    """Konvertiert meta.params in ein AWX Survey-Spec-Objekt.

    Gibt None zurück wenn keine params vorhanden.
    """
    params = meta.get("params")
    if not params:
        return None

    questions = []
    for p in params:
        name = p.get("name", "")
        if not name:
            continue
        questions.append({
            "question_name": name,
            "question_description": p.get("description", name),
            "required": True,
            "type": "text",
            "variable": name,
            "default": str(p.get("example", "")),
            "min": 0,
            "max": 1024,
        })

    if not questions:
        return None

    return {
        "name": "Parameter",
        "description": "Playbook-Parameter",
        "spec": questions,
    }
