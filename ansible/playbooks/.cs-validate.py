#!/usr/bin/env python3
"""Validiert alle Playbooks im aktuellen Verzeichnis auf gültige cs-meta-Blöcke.

Aufruf (aus dem ansible/-Verzeichnis):
    python3 playbooks/.cs-validate.py          # alle *.yml in playbooks/
    python3 playbooks/.cs-validate.py disk_resize.yml ping.yml

Exit-Code 0 = alle OK, Exit-Code 1 = Verstöße gefunden (CI-tauglich).
"""
import sys
import os

# Liegt unter ansible/playbooks/ → Repo-Root ist zwei Ebenen höher.
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

try:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))
    from app.services.playbook_meta import parse_meta, validate_meta
except ImportError:
    # Fallback: direkter Import des Moduls, wenn backend nicht als Paket im Pfad ist
    sys.path.insert(0, os.path.join(_REPO_ROOT, "backend", "app", "services"))
    from playbook_meta import parse_meta, validate_meta  # type: ignore

SKIP = {".cs-validate.py"}


def check_file(path: str) -> list[str]:
    errors: list[str] = []
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return [f"Datei nicht lesbar: {e}"]

    meta = parse_meta(text)
    if meta is None:
        errors.append("Kein cs-meta-Block gefunden")
        return errors

    errors.extend(validate_meta(meta))
    return errors


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))

    if len(sys.argv) > 1:
        files = [os.path.join(base, f) if not os.path.isabs(f) else f for f in sys.argv[1:]]
    else:
        files = sorted(
            os.path.join(base, f)
            for f in os.listdir(base)
            if f.endswith(".yml") and f not in SKIP
        )

    if not files:
        print("Keine Playbooks gefunden.")
        return 0

    total_errors = 0
    for path in files:
        name = os.path.basename(path)
        errs = check_file(path)
        if errs:
            print(f"FAIL  {name}")
            for e in errs:
                print(f"      • {e}")
            total_errors += len(errs)
        else:
            print(f"OK    {name}")

    print()
    if total_errors:
        print(f"{total_errors} Fehler in {sum(1 for f in files if check_file(f))} Datei(en). Bitte cs-meta-Block ergänzen.")
        return 1
    print(f"Alle {len(files)} Playbook(s) haben einen validen cs-meta-Block.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
