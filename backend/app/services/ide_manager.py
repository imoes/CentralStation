"""Werkbank Web-IDE — thin shim that delegates to userenv_manager.

All per-user containers are now unified (code-server + Hermes in one container).
This module keeps the same public API so app/api/ide.py needs no changes.
Old cs-ide-* containers are reaped during the migration period.
"""
from __future__ import annotations

import logging

from app.services.userenv_manager import (
    WORKSPACES_DIR,
    container_name,
    config_volume_name,
    workspace_dir,
    vscode_dir,
    ide_upstream as upstream,
    touch,
    ensure_container,
    exec_sh,
)

log = logging.getLogger(__name__)


def reap_idle(max_idle_seconds: float) -> int:
    """Stop idle userenv containers; also clean up legacy cs-ide-* containers."""
    from app.services import userenv_manager
    import docker
    import time

    stopped = userenv_manager.reap_idle(max_idle_seconds)

    # Migration cleanup: stop any remaining old-style cs-ide-* containers.
    try:
        cli = docker.from_env()
        for c in cli.containers.list(filters={"label": "cs-ide=1"}):
            try:
                c.stop(timeout=10)
                stopped += 1
                log.info("ide_manager: stopped legacy container %s", c.name)
            except Exception as exc:
                log.warning("ide_manager: could not stop legacy %s: %s", c.name, exc)
    except Exception:
        pass

    return stopped
