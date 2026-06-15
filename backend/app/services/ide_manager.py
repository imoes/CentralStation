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
# Env file sourced by /root/.bashrc — carries the auth tokens for the CLI agents
# (claude, codex) so the integrated terminal works without an OAuth browser flow.
# Rewritten on every ensure so token refreshes reach freshly opened terminals.
AGENT_ENV_FILE = "/root/.cs-agent-env.sh"

# In-memory last-activity tracker for the idle reaper (best-effort; resets on
# backend restart, which is fine — the reaper just won't reap until next touch).
_last_used: dict[str, float] = {}


def _client():
    import docker  # lazy import so the backend still boots if SDK/socket absent
    return docker.from_env()


def container_name(user_id: str) -> str:
    return f"cs-ide-{user_id}"


def _user_base(user_id: str) -> str:
    """Host-side directory that holds all bind-mount subdirs for one user."""
    return os.path.join(IDE_WORKSPACES_BASE, user_id)


def workspace_dir(user_id: str) -> str:
    return os.path.join(_user_base(user_id), "workspaces")


def vscode_dir(user_id: str) -> str:
    """VS Code extensions + settings — bind-mount so it's easy to back up."""
    return os.path.join(_user_base(user_id), "vscode")


def config_volume_name(user_id: str) -> str:
    return f"cs-ide-cfg-{user_id}"


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


def ensure_container(user_id: str, claude_token: str | None = None, codex_token: str | None = None) -> str:
    """Ensure the user's code-server container is running. Returns the upstream
    host:port for nginx. Raises on failure.

    claude_token / codex_token, if given, are written into AGENT_ENV_FILE so the
    integrated terminal's `claude` / `codex` CLIs are authenticated without an
    interactive OAuth browser flow (the loopback callback is unreachable from a
    containerised IDE)."""
    cli = _client()
    name = container_name(user_id)

    existing = None
    try:
        existing = cli.containers.get(name)
    except Exception:
        existing = None  # not found → create fresh below

    if existing is not None:
        if existing.status != "running":
            existing.start()
            _wait_ready(existing)
        touch(user_id)
        _write_agent_env(user_id, claude_token, codex_token)
        return upstream(user_id)

    ws_path = workspace_dir(user_id)
    vs_path = vscode_dir(user_id)
    os.makedirs(ws_path, exist_ok=True)
    os.makedirs(vs_path, exist_ok=True)

    volumes = {
        # All per-user bind mounts live under IDE_WORKSPACES_BASE/<uid>/ so a
        # single rsync/tar of that directory backs up workspaces + VS Code state.
        ws_path: {"bind": WORKSPACES_DIR, "mode": "rw"},
        vs_path: {"bind": "/root/.local/share/code-server", "mode": "rw"},
        # Claude Code credentials on a named volume (contains auth tokens, not
        # user-created content — no backup value, keep separate).
        config_volume_name(user_id): {"bind": "/root/.claude", "mode": "rw"},
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
    if claude_token:
        environment["CLAUDE_CODE_OAUTH_TOKEN"] = claude_token

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
    _write_agent_env(user_id, claude_token, codex_token)
    log.info("ide_manager: started %s", name)
    return upstream(user_id)


def _write_agent_env(user_id: str, claude_token: str | None, codex_token: str | None) -> None:
    """Write AGENT_ENV_FILE (mode 600) inside the container with the agent CLI
    tokens. Sourced by /root/.bashrc → every new terminal is authenticated.
    Best-effort: a failure here must not break IDE startup."""
    if not claude_token and not codex_token:
        return
    # Tokens are passed via exec env (never argv) and single-quoted in the file.
    # Claude/OpenAI OAuth tokens are [A-Za-z0-9._-], so single-quoting is safe.
    lines = ["umask 077"]
    env: dict[str, str] = {}
    if claude_token:
        env["CS_CLAUDE_TOKEN"] = claude_token
        lines.append(f"printf \"export CLAUDE_CODE_OAUTH_TOKEN='%s'\\n\" \"$CS_CLAUDE_TOKEN\" >  {AGENT_ENV_FILE}")
    else:
        lines.append(f": > {AGENT_ENV_FILE}")
    if codex_token:
        env["CS_CODEX_TOKEN"] = codex_token
        lines.append(f"printf \"export OPENAI_API_KEY='%s'\\n\" \"$CS_CODEX_TOKEN\" >> {AGENT_ENV_FILE}")
    lines.append(f"chmod 600 {AGENT_ENV_FILE}")
    script = "; ".join(lines)
    try:
        code, out = exec_sh(user_id, script, env)
        if code != 0:
            log.warning("ide_manager: agent-env write failed (%s): %s", code, out[-200:])
    except Exception as e:
        log.warning("ide_manager: agent-env write error: %s", e)


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
