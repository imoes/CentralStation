"""Per-user unified Werkbank+Hermes container orchestration.

Each user gets one `cs-userenv-<user_id>` container that runs both:
  - code-server (VS Code in the browser) on port 8080
  - Hermes/CentralCore (FastAPI) on port 8001

SSH config (user, key) is injected at session creation via configure_ssh().
All Docker SDK calls are synchronous; callers wrap them in asyncio.to_thread.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

USERENV_IMAGE   = os.getenv("USERENV_IMAGE",   "centralstation-userenv:latest")
USERENV_NETWORK = os.getenv("USERENV_NETWORK", "centralstation_default")
USERENV_HOST_SSH_DIR = os.getenv("IDE_HOST_SSH_DIR", "")  # reuse IDE env var for compat
USERENV_ANSIBLE_PATH = os.getenv("IDE_ANSIBLE_PATH", "")
# Host-side base dir for workspace + vscode bind mounts (same as ide_manager).
USERENV_WORKSPACES_BASE = os.getenv("IDE_WORKSPACES_BASE", "/opt/centralstation/ide-workspaces")
# Config file for Hermes (hermes_config.yaml path on host)
USERENV_CONFIG_PATH = os.getenv("USERENV_CONFIG_PATH", "")
WORKSPACES_DIR = "/root/workspaces"

_last_used: dict[str, float] = {}


def _client():
    import docker
    return docker.from_env()


def container_name(user_id: str) -> str:
    return f"cs-userenv-{user_id}"


def _user_base(user_id: str) -> str:
    return os.path.join(USERENV_WORKSPACES_BASE, user_id)


def hermes_config_path(user_id: str) -> str:
    """Per-user hermes_config.yaml path on the host."""
    return os.path.join(_user_base(user_id), "hermes_config.yaml")


def write_hermes_config(user_id: str, extra_servers: dict) -> str:
    """Generate per-user hermes_config.yaml from DB-loaded connectors.

    Always includes centralstation (system server). Adds user-specific servers
    (vibemk, awx-ng, ...) from extra_servers.

    Args:
        extra_servers: {name: {transport, url, headers?}} from user's connector DB rows.
    Returns:
        Path to the written config file.
    """
    import yaml

    backend_url = os.getenv("CENTRALSTATION_BACKEND_URL", "http://backend:8000")
    servers: dict = {
        "centralstation": {
            "transport": "sse",
            "url": f"{backend_url}/api/mcp/sse",
        }
    }
    servers.update(extra_servers)

    config_path = hermes_config_path(user_id)
    os.makedirs(_user_base(user_id), exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump({"mcp_servers": servers}, f, default_flow_style=False, allow_unicode=True)
    log.info("hermes_config written: %s (%d servers: %s)",
             config_path, len(servers), list(servers.keys()))
    return config_path


def workspace_dir(user_id: str) -> str:
    return os.path.join(_user_base(user_id), "workspaces")


def vscode_dir(user_id: str) -> str:
    return os.path.join(_user_base(user_id), "vscode")


def config_volume_name(user_id: str) -> str:
    return f"cs-ide-cfg-{user_id}"  # reuse existing volumes so Claude Code creds survive migration


def ide_upstream(user_id: str) -> str:
    """nginx upstream for the Werkbank (code-server on :8080)."""
    return f"{container_name(user_id)}:8080"


def hermes_url(user_id: str) -> str:
    """HTTP URL for the Hermes FastAPI on :8001."""
    return f"http://{container_name(user_id)}:8001"


def touch(user_id: str) -> None:
    _last_used[container_name(user_id)] = time.monotonic()


def _wait_ready(container, timeout: float = 45.0) -> bool:
    """Wait until both Hermes (:8001) and code-server (:8080) are ready."""
    deadline = time.monotonic() + timeout
    hermes_ok = False
    cs_ok = False
    while time.monotonic() < deadline:
        try:
            if not hermes_ok:
                code, _ = container.exec_run(
                    ["curl", "-sf", "-o", "/dev/null", "http://localhost:8001/health"]
                )
                hermes_ok = (code == 0)
            if not cs_ok:
                code, _ = container.exec_run(
                    ["curl", "-sf", "-o", "/dev/null", "-m", "2", "http://localhost:8080/"]
                )
                cs_ok = (code == 0)
            if hermes_ok and cs_ok:
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def ensure_container(user_id: str) -> str:
    """Ensure the user's unified container is running. Returns ide_upstream for nginx.

    Idempotent — safe to call on every Hermes session create or Werkbank proxy hit.
    """
    cli = _client()
    name = container_name(user_id)

    existing = None
    try:
        existing = cli.containers.get(name)
    except Exception:
        existing = None

    if existing is not None:
        if existing.status != "running":
            existing.start()
            _wait_ready(existing)
        touch(user_id)
        return ide_upstream(user_id)

    ws_path = workspace_dir(user_id)
    vs_path = vscode_dir(user_id)
    os.makedirs(ws_path, exist_ok=True)
    os.makedirs(vs_path, exist_ok=True)

    volumes: dict = {
        ws_path: {"bind": WORKSPACES_DIR, "mode": "rw"},
        vs_path: {"bind": "/root/.local/share/code-server", "mode": "rw"},
        config_volume_name(user_id): {"bind": "/root/.claude", "mode": "rw"},
        f"hermes-state-{user_id}": {"bind": "/root/.hermes", "mode": "rw"},
    }
    # Mount per-user hermes_config.yaml to /app/hermes_config.yaml (NOT into the
    # hermes-state volume at /root/.hermes — Docker volume mounts shadow file bind-mounts
    # when the volume already contains the same file name). The entrypoint copies
    # /app/hermes_config.yaml → /root/.hermes/config.yaml at startup.
    _user_cfg = hermes_config_path(user_id)
    _cfg_to_mount = _user_cfg if os.path.isfile(_user_cfg) else (
        USERENV_CONFIG_PATH if USERENV_CONFIG_PATH and os.path.isfile(USERENV_CONFIG_PATH) else None
    )
    if _cfg_to_mount:
        volumes[_cfg_to_mount] = {"bind": "/app/hermes_config.yaml", "mode": "ro"}
    if USERENV_HOST_SSH_DIR:
        volumes[USERENV_HOST_SSH_DIR] = {"bind": "/root/.ssh_host", "mode": "ro"}
    if USERENV_ANSIBLE_PATH:
        volumes[USERENV_ANSIBLE_PATH] = {"bind": f"{WORKSPACES_DIR}/ansible", "mode": "rw"}

    # Extract the backend hostname from the URL so it is always excluded from the proxy.
    # The hostname can vary (e.g. "backend", "centralstation-backend", ...) depending on
    # the Docker Compose service name, so we derive it dynamically instead of hardcoding.
    _backend_url = os.getenv("CENTRALSTATION_BACKEND_URL", "http://backend:8000")
    from urllib.parse import urlparse as _urlparse
    _backend_host = _urlparse(_backend_url).hostname or "backend"
    _no_proxy = os.getenv("NO_PROXY", "localhost,127.0.0.1")
    if _backend_host not in _no_proxy:
        _no_proxy = f"{_backend_host},{_no_proxy}"
    environment = {
        "HOME": "/root",
        "CENTRALSTATION_BACKEND_URL": _backend_url,
        "HTTP_PROXY": os.getenv("HTTP_PROXY", ""),
        "HTTPS_PROXY": os.getenv("HTTPS_PROXY", ""),
        "NO_PROXY": _no_proxy,
        "http_proxy": os.getenv("http_proxy", os.getenv("HTTP_PROXY", "")),
        "https_proxy": os.getenv("https_proxy", os.getenv("HTTPS_PROXY", "")),
        "no_proxy": _no_proxy,
    }

    c = cli.containers.run(
        USERENV_IMAGE,
        name=name,
        detach=True,
        user="0:0",
        environment=environment,
        volumes=volumes,
        network=USERENV_NETWORK,
        labels={"cs-userenv": "1", "cs-userenv-uid": user_id},
        restart_policy={"Name": "no"},
        cap_add=["NET_RAW"],
    )
    _wait_ready(c)
    touch(user_id)
    log.info("userenv_manager: started %s", name)
    return ide_upstream(user_id)


def configure_ssh(user_id: str, username: str, key_pem: str, password: str = "") -> None:
    """Write SSH key + config into the user's running container via exec_run.

    Generates Host * / User <username> / IdentityFile ~/.ssh/user.key
    Called idempotently at each session create — overwrites previous config.
    """
    import docker

    cli = _client()
    name = container_name(user_id)
    try:
        c = cli.containers.get(name)
    except docker.errors.NotFound:
        log.warning("userenv_manager: container %s not found for configure_ssh", name)
        return

    if key_pem and key_pem.strip():
        # Normalise key: strip trailing whitespace, then add exactly one trailing newline.
        # printf '%s' suppresses newlines which breaks OpenSSH ("error in libcrypto").
        # The sed strips any existing trailing blank lines before we append the required \n.
        c.exec_run(
            ["sh", "-c",
             "mkdir -p /root/.ssh && "
             "printf '%s' \"$KEY\" | sed 's/[[:space:]]*$//' > /root/.ssh/user.key && "
             "printf '\\n' >> /root/.ssh/user.key && "
             "chmod 600 /root/.ssh/user.key"],
            environment={"KEY": key_pem},
        )

    ssh_user = username.strip()
    ssh_cfg_lines = ["Host *"]
    if ssh_user:
        ssh_cfg_lines.append(f"    User {ssh_user}")
    if key_pem and key_pem.strip():
        ssh_cfg_lines.append("    IdentityFile /root/.ssh/user.key")
    ssh_cfg_lines += [
        "    StrictHostKeyChecking no",
        "    ConnectTimeout 10",
        "",
    ]
    ssh_cfg = "\n".join(ssh_cfg_lines)
    c.exec_run(
        ["sh", "-c", "mkdir -p /root/.ssh && printf '%s' \"$CFG\" > /root/.ssh/config && chmod 600 /root/.ssh/config"],
        environment={"CFG": ssh_cfg},
    )
    log.info("userenv_manager: SSH configured for %s (user=%s key=%s)",
             name, ssh_user, "yes" if key_pem else "no")


def exec_sh(user_id: str, script: str, environment: dict | None = None) -> tuple[int, str]:
    """Run a /bin/sh -c script as root inside the user's container."""
    cli = _client()
    c = cli.containers.get(container_name(user_id))
    code, out = c.exec_run(["sh", "-c", script], user="root", environment=environment or {})
    return code, (out.decode(errors="replace") if isinstance(out, (bytes, bytearray)) else str(out))


def reap_idle(max_idle_seconds: float) -> int:
    """Stop cs-userenv-* containers idle longer than the threshold."""
    cli = _client()
    stopped = 0
    now = time.monotonic()
    for c in cli.containers.list(filters={"label": "cs-userenv=1"}):
        last = _last_used.get(c.name)
        if last is None:
            _last_used[c.name] = now
            continue
        if now - last > max_idle_seconds:
            try:
                c.stop(timeout=10)
                stopped += 1
                log.info("userenv_manager: reaped idle %s", c.name)
            except Exception as e:
                log.warning("userenv_manager: reap %s failed: %s", c.name, e)
    return stopped
