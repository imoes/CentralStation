"""Installiert Hermes' Laufzeit-Abhängigkeiten auch dann, wenn das Paket sie verschweigt.

Hermes verlangt Python >=3.11,<3.15, versieht aber JEDE seiner Abhängigkeiten mit
`; python_version >= '3.14'`. Unter dem Python 3.13 des Images installiert
`pip install -e /opt/hermes` dadurch Hermes ohne eine einzige Abhängigkeit — der
Import scheitert dann an der ersten (`No module named 'requests'`), und in der
Konsole entsteht schlicht keine Hermes-Sitzung.

Dieses Skript wertet die Bedingungen so aus, als liefe Python 3.14, und installiert,
was dabei übrig bleibt. Alle übrigen Bedingungen gelten unverändert — reine
Windows-Pakete (pywin32, winrt-*) bleiben also draußen. Ist der Fehler oben behoben,
installiert das Skript nichts Neues: pip findet dann alles schon vor.

Aufruf im Dockerfile, direkt nach `pip install -e /opt/hermes`.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement

PYPROJECT = "/opt/hermes/pyproject.toml"


def requirements(pyproject: str = PYPROJECT) -> list[str]:
    """Die Abhängigkeiten aus [project].dependencies, deren Bedingung unter 3.14 gilt."""
    with open(pyproject, "rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    env = {**default_environment(), "python_version": "3.14", "python_full_version": "3.14.0"}
    out: list[str] = []
    for dep in deps:
        req = Requirement(dep)
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        req.marker = None
        out.append(str(req))
    return out


def main() -> int:
    reqs = requirements()
    print(f"hermes_deps: {len(reqs)} Abhängigkeiten", flush=True)
    if not reqs:
        return 0
    return subprocess.call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *reqs])


if __name__ == "__main__":
    sys.exit(main())
