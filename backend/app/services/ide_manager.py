"""Per-user code-server container orchestration for the Werkbank Web-IDE.

The backend starts/stops one `cs-ide-<user_id>` container per user via the Docker
SDK (docker.sock mounted into the backend). Each container runs code-server
(--auth none, no published port — nginx auth_request is the gate), as root with
HOME=/root so it can read the mounted marvin SSH key (same pattern as centralcore).

All Docker SDK calls are synchronous; callers wrap them in asyncio.to_thread.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

IDE_IMAGE = os.getenv("IDE_IMAGE", "centralstation-codeserver:latest")
IDE_NETWORK = os.getenv("IDE_NETWORK", "centralstation_default")
IDE_HOST_SSH_DIR = os.getenv("IDE_HOST_SSH_DIR", "")
IDE_CONTAINER_PORT = os.getenv("IDE_CONTAINER_PORT", "8080")
# Host-side base dir for workspace bind mounts. The backend container must mount
# the same path so os.makedirs() creates the directory on the host filesystem.
IDE_WORKSPACES_BASE = os.getenv("IDE_WORKSPACES_BASE", "/opt/centralstation/ide-workspaces")
WORKSPACES_DIR = "/root/workspaces"

# In-memory last-activity tracker for the idle reaper (best-effort; resets on
# backend restart, which is fine — the reaper just won't reap until next touch).
_last_used: dict[str, float] = {}


def _client():
    import docker  # lazy import so the backend still boots if SDK/socket absent
    return docker.from_env()


def container_name(user_id: str) -> str:
    return f"cs-ide-{user_id}"


def workspace_dir(user_id: str) -> str:
    """Absolute host-side path for the per-user workspace bind mount."""
    return os.path.join(IDE_WORKSPACES_BASE, user_id)


def config_volume_name(user_id: str) -> str:
    return f"cs-ide-cfg-{user_id}"


def vscode_volume_name(user_id: str) -> str:
    """Named volume for VS Code extensions + user settings (persistent)."""
    return f"cs-ide-vsc-{user_id}"


def upstream(user_id: str) -> str:
    return f"{container_name(user_id)}:{IDE_CONTAINER_PORT}"


def touch(user_id: str) -> None:
    _last_used[container_name(user_id)] = time.monotonic()


def _wait_ready(container, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, _ = container.exec_run(
                ["curl", "-sf", "-o", "/dev/null", f"http://localhost:{IDE_CONTAINER_PORT}/healthz"]
            )
            if code == 0:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def ensure_container(user_id: str) -> str:
    """Ensure the user's code-server container is running. Returns the upstream
    host:port for nginx. Raises on failure."""
    cli = _client()
    name = container_name(user_id)

    try:
        c = cli.containers.get(name)
        if c.status != "running":
            c.start()
            _wait_ready(c)
        touch(user_id)
        return upstream(user_id)
    except Exception:
        # Not found (or unusable) → create fresh
        pass

    ws_path = workspace_dir(user_id)
    os.makedirs(ws_path, exist_ok=True)

    volumes = {
        # Bind mount so workspaces live on the host filesystem (easy backup/access).
        ws_path: {"bind": WORKSPACES_DIR, "mode": "rw"},
        # Persists Claude Code credentials (CLAUDE_CONFIG_DIR=/root/.claude).
        config_volume_name(user_id): {"bind": "/root/.claude", "mode": "rw"},
        # Persists VS Code extensions + user settings (settings.json, keybindings).
        vscode_volume_name(user_id): {"bind": "/root/.local/share/code-server", "mode": "rw"},
    }
    if IDE_HOST_SSH_DIR:
        volumes[IDE_HOST_SSH_DIR] = {"bind": "/root/.ssh_host", "mode": "ro"}

    environment = {
        "HOME": "/root",
        "HTTP_PROXY": os.getenv("HTTP_PROXY", ""),
        "HTTPS_PROXY": os.getenv("HTTPS_PROXY", ""),
        # gitlab.ippen.media must bypass the proxy for git push/pull.
        "NO_PROXY": "localhost,127.0.0.1,.ippen.media",
        "http_proxy": os.getenv("HTTP_PROXY", ""),
        "https_proxy": os.getenv("HTTPS_PROXY", ""),
        "no_proxy": "localhost,127.0.0.1,.ippen.media",
    }

    c = cli.containers.run(
        IDE_IMAGE,
        name=name,
        detach=True,
        user="0:0",
        environment=environment,
        volumes=volumes,
        network=IDE_NETWORK,
        labels={"cs-ide": "1", "cs-ide-uid": user_id},
        restart_policy={"Name": "no"},
    )
    _wait_ready(c)
    touch(user_id)
    log.info("ide_manager: started %s", name)
    return upstream(user_id)


def exec_sh(user_id: str, script: str, environment: dict | None = None) -> tuple[int, str]:
    """Run a /bin/sh -c script as root inside the user's container."""
    cli = _client()
    c = cli.containers.get(container_name(user_id))
    code, out = c.exec_run(["sh", "-c", script], user="root", environment=environment or {})
    return code, (out.decode(errors="replace") if isinstance(out, (bytes, bytearray)) else str(out))


def reap_idle(max_idle_seconds: float) -> int:
    """Stop cs-ide-* containers idle longer than the threshold. Returns count stopped."""
    cli = _client()
    stopped = 0
    now = time.monotonic()
    for c in cli.containers.list(filters={"label": "cs-ide=1"}):
        last = _last_used.get(c.name)
        # Unknown last-use (e.g. after backend restart): seed now, skip this round.
        if last is None:
            _last_used[c.name] = now
            continue
        if now - last > max_idle_seconds:
            try:
                c.stop(timeout=10)
                stopped += 1
                log.info("ide_manager: reaped idle %s", c.name)
            except Exception as e:
                log.warning("ide_manager: reap %s failed: %s", c.name, e)
    return stopped
