"""Per-user unified Werkbank+Hermes container orchestration.

Each user gets one `cs-userenv-<user_id>` container that runs both:
  - code-server (VS Code in the browser) on port 8080
  - Hermes (FastAPI) on port 8001

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
WORKSPACES_DIR = "/home/yolo/workspaces"
_YOLO_HOME = "/home/yolo"

# ── Playwright MCP (shared stdio server for all Console agents) ────────────
# The `playwright-mcp` binary and the Chromium browser are baked into the image
# (see userenv/Dockerfile). --no-sandbox is required because the container has no
# user-namespace sandbox; --headless because there is no display.
_PLAYWRIGHT_MCP_CMD = "playwright-mcp"
_PLAYWRIGHT_MCP_ARGS = [
    "--browser", "chromium",
    # --browser chromium alone maps to the missing "chrome-for-testing" channel;
    # point at the Chromium binary we baked in (stable symlink from the Dockerfile).
    "--executable-path", "/opt/ms-playwright/chrome-stable",
    "--headless", "--no-sandbox", "--isolated",
]

_last_used: dict[str, float] = {}
# Names of containers that ensure_container freshly `run`-created (as opposed to
# started an existing one). The per-container ~/.ssh (config + user.key) is NOT on a
# persistent volume, so a freshly created container has only the entrypoint fallback
# SSH config until configure_ssh runs again. DB-aware callers (send_message,
# get_history) consume this flag to re-apply SSH creds after an on-demand recreation.
_just_created: set[str] = set()


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
            # Deepsearch (search_knowledge_base deepsearch=True) can run up to ~300s.
            # Give the per-tool-call timeout headroom above that so Hermes doesn't
            # abort a legitimate long-running deepsearch.
            "timeout": 330,
        },
        # Browser automation — stdio command server (no transport/url).
        "playwright": {
            "command": _PLAYWRIGHT_MCP_CMD,
            "args": list(_PLAYWRIGHT_MCP_ARGS),
        },
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


_YOLO_MIGRATION_MARKER = ".yolo-migrated"


def _migrate_to_yolo(ws_path: str, vs_path: str) -> None:
    """One-time recursive chown of bind-mount dirs from root (0) to yolo (1000).

    Triggered by the absence of a marker file. Runs inline (fast for vscode/,
    potentially slower for workspaces/ with many files — still typically <1s for
    normal repo sizes). Writes the marker when done so it never runs again.
    """
    for dirpath in (vs_path, ws_path):
        marker = os.path.join(dirpath, _YOLO_MIGRATION_MARKER)
        if os.path.exists(marker):
            continue
        # Check if top-level is still root-owned — if it's already 1000, skip.
        try:
            if os.stat(dirpath).st_uid == 1000:
                open(marker, "w").close()
                continue
        except OSError:
            continue
        # Recursively chown everything to yolo (1000).
        try:
            for root, dirs, files in os.walk(dirpath):
                try:
                    os.chown(root, 1000, 1000)
                except OSError:
                    pass
                for fname in dirs + files:
                    try:
                        os.chown(os.path.join(root, fname), 1000, 1000)
                    except OSError:
                        pass
            open(marker, "w").close()
            log.info("userenv_manager: migrated %s to yolo (1000)", dirpath)
        except Exception as exc:
            log.warning("userenv_manager: yolo migration failed for %s: %s", dirpath, exc)


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
    # chown to yolo (UID 1000) so the non-root container user can write to these dirs.
    for _p in (ws_path, vs_path):
        try:
            os.chown(_p, 1000, 1000)
        except OSError:
            pass
    # ── root→yolo migration (one-time per user) ───────────────────────────────
    # Old containers ran as root (UID 0); new ones run as yolo (UID 1000).
    # If existing workspace/vscode files are still owned by root, yolo cannot
    # edit them. A marker file prevents this from running on every container start.
    _migrate_to_yolo(ws_path, vs_path)

    volumes: dict = {
        ws_path: {"bind": WORKSPACES_DIR, "mode": "rw"},
        vs_path: {"bind": f"{_YOLO_HOME}/.local/share/code-server", "mode": "rw"},
        config_volume_name(user_id): {"bind": f"{_YOLO_HOME}/.claude", "mode": "rw"},
        f"hermes-state-{user_id}": {"bind": f"{_YOLO_HOME}/.hermes", "mode": "rw"},
        f"cs-pip-{user_id}": {"bind": f"{_YOLO_HOME}/pip", "mode": "rw"},
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
        volumes[USERENV_HOST_SSH_DIR] = {"bind": f"{_YOLO_HOME}/.ssh_host", "mode": "ro"}
    if USERENV_ANSIBLE_PATH:
        volumes[USERENV_ANSIBLE_PATH] = {"bind": f"{WORKSPACES_DIR}/ansible", "mode": "rw"}

    # Extract the backend hostname from the URL so it is always excluded from the proxy.
    # The hostname can vary (e.g. "backend", "centralstation-backend", ...) depending on
    # the Docker Compose service name, so we derive it dynamically instead of hardcoding.
    _backend_url = os.getenv("CENTRALSTATION_BACKEND_URL", "http://backend:8000")
    from urllib.parse import urlparse as _urlparse
    _backend_host = _urlparse(_backend_url).hostname or "backend"
    from app.core.domains import internal_domains as _int_domains
    _default_no_proxy = ",".join(["localhost", "127.0.0.1"] + [f".{d}" for d in _int_domains()])
    _no_proxy = os.getenv("NO_PROXY", _default_no_proxy)
    if _backend_host not in _no_proxy:
        _no_proxy = f"{_backend_host},{_no_proxy}"
    environment = {
        "HOME": _YOLO_HOME,
        "CS_USER_ID": user_id,
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
        user="1000:1000",
        environment=environment,
        volumes=volumes,
        network=USERENV_NETWORK,
        labels={"cs-userenv": "1", "cs-userenv-uid": user_id},
        # unless-stopped: survive Docker-daemon restarts and host reboots so the
        # Console/Werkbank container comes back automatically instead of only on the
        # next on-demand access. The idle reaper's explicit stop() is still honored
        # (unless-stopped does not auto-restart an explicitly stopped container).
        restart_policy={"Name": "unless-stopped"},
        cap_add=["NET_RAW"],
        extra_hosts={"host.docker.internal": "host-gateway"},
    )
    _wait_ready(c)
    touch(user_id)
    _just_created.add(name)  # signal callers to re-apply SSH/agent creds
    log.info("userenv_manager: started %s", name)
    return ide_upstream(user_id)


def consume_just_created(user_id: str) -> bool:
    """Return True (once) if ensure_container freshly created this user's container.

    Clears the flag so the caller reconfigures exactly once per recreation.
    """
    name = container_name(user_id)
    if name in _just_created:
        _just_created.discard(name)
        return True
    return False


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
             "mkdir -p $HOME/.ssh && "
             "printf '%s' \"$KEY\" | sed 's/[[:space:]]*$//' > $HOME/.ssh/user.key && "
             "printf '\\n' >> $HOME/.ssh/user.key && "
             "chmod 600 $HOME/.ssh/user.key"],
            environment={"KEY": key_pem},
        )

    ssh_user = username.strip() or "marvin"
    # Internal hosts resolve via sssd on the Docker host (127.0.1.1 alias). ProxyJump
    # through host.docker.internal lets the container piggy-back on the host's sssd
    # infrastructure without needing domain-join inside the container.
    #
    # WHICH hosts those are is deployment data, not source: it comes from
    # CS_INTERNAL_DOMAINS in the gitignored .env. Hardcoding a domain here once broke
    # SSH outright — the pattern stopped matching the real hosts, they fell through to
    # the "Host *" block, and every connection lost its ProxyJump.
    from app.core.domains import internal_domains
    # Only real estate domains get the ProxyJump. The generic suffixes "internal" and
    # "local" are for recognising hostnames in text — routing SSH through them is
    # actively harmful: the jump host itself is host.docker.internal, which matches
    # *.internal, so it would proxy through itself ("jumphost loop").
    ssh_domains = [d for d in internal_domains() if d not in ("internal", "local")]
    host_patterns = " ".join(f"*.{d}" for d in ssh_domains)
    ssh_cfg_lines = [
        # Belt and braces: never proxy the jump host through itself, whatever the
        # configured domains happen to match.
        "Host host.docker.internal",
        f"    User {ssh_user}",
        "    ProxyJump none",
        "    StrictHostKeyChecking no",
        "",
    ]
    # The whole internal-host stanza is conditional. Appending its body unconditionally
    # let those lines fall into the preceding host.docker.internal block whenever no
    # estate domain was configured — the default, since internal/local are filtered out
    # above. ssh_config keeps the FIRST value per keyword, so "ProxyJump none" still
    # won and nothing looped, but the config was malformed and the internal-host block
    # vanished without a word.
    if host_patterns:
        ssh_cfg_lines += [
            f"Host {host_patterns}",
            f"    User {ssh_user}",
        ]
        if key_pem and key_pem.strip():
            ssh_cfg_lines.append(f"    IdentityFile {_YOLO_HOME}/.ssh/user.key")
        ssh_cfg_lines += [
            f"    ProxyJump {ssh_user}@host.docker.internal",
            "    StrictHostKeyChecking no",
            "    ConnectTimeout 15",
            "",
        ]
    else:
        log.warning(
            "configure_ssh: no estate domain in CS_INTERNAL_DOMAINS — internal hosts "
            "get no ProxyJump and will only work if reachable directly"
        )
    ssh_cfg_lines += [
        "Host *",
        f"    User {ssh_user}",
    ]
    if key_pem and key_pem.strip():
        ssh_cfg_lines.append(f"    IdentityFile {_YOLO_HOME}/.ssh/user.key")
    ssh_cfg_lines += [
        "    StrictHostKeyChecking no",
        "    ConnectTimeout 10",
        "",
    ]
    ssh_cfg = "\n".join(ssh_cfg_lines)
    c.exec_run(
        ["sh", "-c", "mkdir -p $HOME/.ssh && printf '%s' \"$CFG\" > $HOME/.ssh/config && chmod 600 $HOME/.ssh/config"],
        environment={"CFG": ssh_cfg},
    )
    log.info("userenv_manager: SSH configured for %s (user=%s key=%s)",
             name, ssh_user, "yes" if key_pem else "no")
    configure_claude_md(user_id, ssh_user)


def configure_claude_md(user_id: str, ssh_user: str = "marvin") -> None:
    """Write ~/.claude/CLAUDE.md into the container (on cs-ide-cfg volume → persistent).

    Provides Claude CLI with the same environment context that Hermes gets via system
    prompt: SSH instructions, workspace location, site topology. Read automatically
    by every claude CLI invocation as the global user-level CLAUDE.md.

    The example hostnames are built from CS_INTERNAL_DOMAINS (gitignored .env) — an
    agent told to `ssh host.example.com` when the estate is somewhere else wastes its
    first turns on hosts that do not exist.
    """
    import docker as _docker
    from app.core.domains import internal_domains
    name = container_name(user_id)
    try:
        c = _client().containers.get(name)
    except _docker.errors.NotFound:
        return

    _domains = [d for d in internal_domains() if d not in ("internal", "local")] or list(internal_domains())
    _dom = _domains[-1]          # broadest suffix (sorted longest-first)
    _dom_list = ", ".join(_domains)

    content = f"""# CentralStation — Linux-Admin-Umgebung

Du bist ein Linux-Sysadmin-Assistent im CentralStation-Userenv-Container.
SSH-Zugriff auf alle Server dieser Domains ist vorkonfiguriert: {_dom_list}

## SSH-ZUGRIFF
Befehl: `ssh <hostname>.{_dom} '<befehl>'`
User und Key sind per ~/.ssh/config voreingestellt — kein -i, -u oder -o IdentityFile nötig.
SSH-User: `{ssh_user}`

Beispiele:
```bash
ssh hal.{_dom} 'hostname && df -h'
ssh docker0218.{_dom} 'docker ps'
ssh vpp0221.{_dom} 'free -h; uptime'
```

## WORKSPACE
Alle Dateien, Skripte und Artefakte immer in `/home/yolo/workspaces/` ablegen — niemals in /tmp.

## KRITISCHE REGEL: READ-ONLY — NIEMALS UNGEFRAGT SCHREIBEN
Du arbeitest standardmäßig NUR LESEND (Diagnose). Führe NIEMALS eigenständig eine
Operation aus, die ein System verändert. Verändernde Operationen sind u.a.:
- Dienste: `systemctl restart|stop|start|reload|enable|disable`, `service ... restart`, reboot, shutdown
- Dateien: `rm`, `mv`, `cp`, `chmod`, `chown`, `sed -i`, `tee`, `nano/vim`, Umleitung mit `>`/`>>` in echte Dateien
- Pakete: `apt/yum/dnf install|remove|upgrade`, `pip/npm install`
- Container/Cluster: `docker restart|stop|rm`, `kubectl apply|delete|scale`
- Git: `git push|commit|reset|checkout`
- Nutzer/Netz: `useradd`, `passwd`, `iptables`, `crontab`
Das gilt auch INNERHALB von `ssh <host> '<befehl>'` — der entfernte Befehl zählt.

Ablauf bei nötiger Änderung:
1. NICHT ausführen.
2. Beschreibe dem Nutzer die geplante Änderung: genauer Befehl, Zielsystem, erwartete Wirkung.
3. Frage EXPLIZIT um Erlaubnis und STOPPE.
4. Erst wenn der Nutzer in einer Folgenachricht ausdrücklich zustimmt ('ja', 'mach das',
   'führe aus'), darfst du die Operation ausführen.
(Ein Sicherheits-Hook blockiert solche Befehle zusätzlich automatisch — versuche NICHT,
ihn zu umgehen. Reine Lese-Diagnose wie df, cat, journalctl, `systemctl status`, docker ps
ist jederzeit erlaubt.)

## WEITERE REGELN
- SSH-Fehler sofort und vollständig melden (exit code + stderr), nicht ausweichen
- subprocess.run() immer mit timeout=120 aufrufen
"""

    c.exec_run(
        ["sh", "-c", "mkdir -p $HOME/.claude && printf '%s' \"$MD\" > $HOME/.claude/CLAUDE.md"],
        environment={"MD": content},
    )
    log.info("userenv_manager: CLAUDE.md written for %s", name)


def _expires_to_ms(expires_at) -> int:
    """Convert an expiry (ISO-8601 string or ms-since-epoch int/str) to ms, or 0.

    The DB stores expiry as an ISO-8601 string (_claude_expires_at_iso); the CLI's
    .credentials.json stores it as ms since epoch. int() on an ISO string throws —
    the old bug that always fell back to a fake now+1h expiry.
    """
    if not expires_at:
        return 0
    try:
        return int(expires_at)  # already ms since epoch
    except (ValueError, TypeError):
        pass
    try:
        import datetime as _dt
        s = str(expires_at).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def read_claude_oauth(user_id: str) -> dict | None:
    """Read the claudeAiOauth block from the container's ~/.claude/.credentials.json.

    Returns the dict (accessToken/refreshToken/expiresAt/…) or None. The native
    `claude` CLI refreshes and rotates this token in place, so this is the source of
    truth for the live token — the backend syncs it back into the DB.
    """
    import json
    try:
        c = _client().containers.get(container_name(user_id))
        rr = c.exec_run(["sh", "-c", "cat $HOME/.claude/.credentials.json 2>/dev/null || echo '{}'"])
        data = json.loads(rr.output.decode(errors="replace"))
        oauth = data.get("claudeAiOauth")
        if isinstance(oauth, dict) and oauth.get("accessToken"):
            return oauth
    except Exception:
        pass
    return None


def configure_claude_credentials(
    user_id: str, access_token: str, refresh_token: str, expires_at: str | None,
    extra_servers: dict | None = None, force_overwrite: bool = False,
) -> None:
    """Write ~/.claude/.credentials.json into the container (on cs-ide-cfg volume → persistent).

    Format matches what `claude auth login --claudeai` writes. Because the file lives on the
    cs-ide-cfg named volume it survives container restarts — no re-injection needed.
    Also configures the Claude Code VS Code extension in code-server automatically.

    Reads the existing credentials file first so that extra fields written by the VS Code
    extension (scopes, subscriptionType, rateLimitTier) are preserved — claude CLI requires
    these fields to recognise the credential as valid. Only the three token fields are updated.
    expiresAt is stored as an integer (milliseconds since epoch) as the CLI expects.
    """
    import json
    import time
    import docker as _docker

    # Real expiry in ms (parse ISO or int). NEVER fabricate a now+1h expiry: the old
    # code did int(<ISO string>) which always threw and fell back to now+1h, so the CLI
    # believed the 8h token expired after 1h and refreshed hourly — every native refresh
    # rotated the refresh token, which our subsequent volume overwrite then clobbered,
    # breaking the rotation chain (=> daily "must re-authenticate").
    incoming_ms = _expires_to_ms(expires_at)
    _now_ms = int(time.time() * 1000)
    if incoming_ms <= 0:
        incoming_ms = _now_ms + 8 * 3_600_000  # sane default: assume a full 8h token

    try:
        c = _client().containers.get(container_name(user_id))

        # Read existing credentials file so we can preserve extra fields.
        existing: dict = {}
        read_result = c.exec_run(
            ["sh", "-c", "cat $HOME/.claude/.credentials.json 2>/dev/null || echo '{}'"],
        )
        try:
            existing = json.loads(read_result.output.decode(errors="replace"))
        except Exception:
            existing = {}

        # Merge: start from existing claudeAiOauth dict, update only token fields.
        oauth = existing.get("claudeAiOauth") or {}

        # Let the native CLI own the token lifecycle. The `claude` CLI refreshes and
        # ROTATES the refresh token in .credentials.json (8h access token, ~28-day
        # rotating refresh chain). If we blindly overwrite that with the DB copy, we
        # replace a freshly-rotated token with a stale one whose refresh token was
        # already consumed → next refresh 401 → daily re-auth. So only seed/overwrite
        # the volume when: it has no token yet, its token is already expired, OR the
        # incoming token is strictly newer (fresh OAuth / force_overwrite for account
        # switch). Otherwise keep the CLI-managed volume token untouched.
        try:
            vol_ms = int(oauth.get("expiresAt") or 0)
        except (ValueError, TypeError):
            vol_ms = 0
        vol_has_token = bool(oauth.get("accessToken"))
        keep_volume = (
            not force_overwrite
            and vol_has_token
            and vol_ms > _now_ms          # volume token still valid
            and vol_ms >= incoming_ms     # and not older than the DB copy
        )
        if keep_volume:
            log.info("userenv_manager: keeping CLI-managed volume claude token for %s "
                     "(vol expiry %d >= db %d, not expired)", container_name(user_id), vol_ms, incoming_ms)
        else:
            oauth.update({
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresAt": incoming_ms,
            })
        oauth.setdefault("scopes", [
            "user:file_upload", "user:inference", "user:mcp_servers",
            "user:profile", "user:sessions:claude_code",
        ])
        oauth.setdefault("subscriptionType", "pro")
        oauth.setdefault("rateLimitTier", "default_raven")
        existing["claudeAiOauth"] = oauth
        creds = json.dumps(existing)

        c.exec_run(
            ["sh", "-c",
             "mkdir -p $HOME/.claude && "
             "printf '%s' \"$C\" > $HOME/.claude/.credentials.json && "
             "chmod 600 $HOME/.claude/.credentials.json"],
            environment={"C": creds},
        )
        log.info("userenv_manager: claude credentials written for %s", container_name(user_id))

        # Register personal MCP servers (extra_servers) + centralstation in .claude.json.
        # centralstation is always added; extra_servers come from the user's connector_configs
        # (type=mcp_server) — same set that Hermes and Codex get.
        mcp_to_register: dict = {
            "centralstation": {
                "transport": "streamable-http",
                "url": f"{os.getenv('CENTRALSTATION_BACKEND_URL', 'http://backend:8000').rstrip('/')}/api/mcp-http/",
            },
            **(extra_servers or {}),
        }
        for srv_name, srv_cfg in mcp_to_register.items():
            url = srv_cfg.get("url", "")
            if not url:
                continue
            transport = "http" if "http" in srv_cfg.get("transport", "streamable-http") else "sse"
            cmd = ["claude", "mcp", "add", "--transport", transport, "--scope", "user", srv_name, url]
            # Add auth header if the server requires a bearer token
            token_header = (srv_cfg.get("headers") or {}).get("Authorization", "")
            if token_header:
                cmd += ["--header", f"Authorization: {token_header}"]
            c.exec_run(cmd)
            log.info("userenv_manager: MCP server '%s' registered for %s", srv_name, container_name(user_id))

        # Playwright — stdio command server (browser automation). The `--` separates
        # claude's flags from the server command + its args.
        pw_cmd = ["claude", "mcp", "add", "--scope", "user", "playwright", "--",
                  _PLAYWRIGHT_MCP_CMD, *_PLAYWRIGHT_MCP_ARGS]
        c.exec_run(pw_cmd)
        log.info("userenv_manager: MCP server 'playwright' registered for %s", container_name(user_id))
    except _docker.errors.NotFound:
        log.warning("configure_claude_credentials: container %s not found", container_name(user_id))


def _codex_config_toml(mcp_servers: dict | None) -> str:
    """Render ~/.codex/config.toml: ChatGPT-backend provider + MCP servers.

    mcp_servers maps name → {transport, url, headers?} (same shape as the Hermes
    config). Each becomes an [mcp_servers.<name>] entry with the streamable-http URL
    and default_tools_approval_mode="approve" so `codex exec` runs tool calls without
    an interactive prompt (in headless exec mode the approval reader gets EOF and
    would otherwise auto-cancel every MCP tool call → "user cancelled MCP tool call").
    """
    backend_url = os.getenv("CENTRALSTATION_BACKEND_URL", "http://backend:8000").rstrip("/")
    parts = [
        'model = "gpt-5.5"',
        'model_provider = "chatgpt_backend"',
        # Shell-command governance (separate from MCP tool approval below):
        # danger-full-access: disables bwrap sandboxing so SSH and other network
        # commands work from the container (bwrap requires unprivileged user
        # namespaces which Docker containers don't have by default).
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        '',
        '[model_providers.chatgpt_backend]',
        'name = "ChatGPT Backend"',
        'base_url = "https://chatgpt.com/backend-api/codex"',
        'wire_api = "responses"',
        'env_key = "OPENAI_API_KEY"',
        '',
        # CentralStation IT-Ops tools via the native streamable-http MCP endpoint
        # (/api/mcp-http/ — codex speaks streamable-http, not the legacy SSE app).
        '[mcp_servers.centralstation]',
        f'url = "{backend_url}/api/mcp-http/"',
        'default_tools_approval_mode = "approve"',
        # search_knowledge_base(deepsearch=True) can run up to ~300s — give headroom.
        'tool_timeout_sec = 330',
        '',
        # Playwright — stdio command server (browser automation).
        '[mcp_servers.playwright]',
        f'command = "{_PLAYWRIGHT_MCP_CMD}"',
        'args = [' + ", ".join(f'"{a}"' for a in _PLAYWRIGHT_MCP_ARGS) + ']',
        'default_tools_approval_mode = "approve"',
        'tool_timeout_sec = 120',
    ]
    # Personal MCP connectors (e.g. VibeMK) — only streamable-http servers; codex
    # connects to their URL directly. Bearer tokens (if any) are passed via env var.
    for name, srv in (mcp_servers or {}).items():
        url = (srv.get("url") or "").rstrip("/")
        if not url or name == "centralstation":
            continue
        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
        parts += [
            '',
            f'[mcp_servers.{safe}]',
            f'url = "{url}"',
            'default_tools_approval_mode = "approve"',
            'tool_timeout_sec = 60',
        ]
    return "\n".join(parts) + "\n"


def configure_codex_credentials(
    user_id: str, access_token: str, mcp_servers: dict | None = None
) -> None:
    """Configure @openai/codex CLI for the user's ChatGPT-account OAuth token.

    The ChatGPT OAuth access token (aud=api.openai.com/v1) is NOT a paid API key and
    NOT a codex "agent identity JWT", so neither `OPENAI_API_KEY` against api.openai.com
    nor `codex login --with-access-token` work. The token *does* work as a plain Bearer
    against https://chatgpt.com/backend-api/codex/responses (same as the Hermes codex LLM
    mode). We therefore point codex at that backend via a custom model_provider in
    ~/.codex/config.toml and feed the token through OPENAI_API_KEY (env_key).

    Writes:
      - /root/.profile      → export OPENAI_API_KEY (token store, sourced by `codex exec`)
      - /root/.codex/config.toml → ChatGPT-backend provider + MCP servers (centralstation
        via /api/mcp-http/ streamable-http + the user's personal MCP connectors)

    Not on a named volume → re-injected at each session create (same pattern as SSH).
    """
    import docker as _docker

    config_toml = _codex_config_toml(mcp_servers)
    try:
        c = _client().containers.get(container_name(user_id))
        # CODEX_HOME lives under yolo's home: the codex subprocess runs as yolo
        # (uid 1000) and cannot read /root. main.py sources $CODEX_HOME/env for the
        # OPENAI_API_KEY and sets CODEX_HOME so config.toml is picked up.
        code, out = c.exec_run(
            ["sh", "-c",
             "mkdir -p /home/yolo/.codex && "
             # OPENAI_API_KEY into $CODEX_HOME/env (sourced by main.py before `codex exec`)
             "printf 'export OPENAI_API_KEY=\"%s\"\\n' \"$K\" > /home/yolo/.codex/env && "
             # provider + MCP config so codex talks to the ChatGPT backend and tools
             "printf '%s' \"$CFG\" > /home/yolo/.codex/config.toml"],
            environment={"K": access_token, "CFG": config_toml},
        )
        if code != 0:
            log.warning("configure_codex_credentials: write failed (%s): %s",
                        code, (out or b"").decode(errors="replace")[:200])
        else:
            log.info("userenv_manager: codex credentials + config.toml written for %s",
                     container_name(user_id))
    except _docker.errors.NotFound:
        log.warning("configure_codex_credentials: container %s not found", container_name(user_id))


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


# ── Write approval (read-only guard) ──────────────────────────────────────────
#: The PreToolUse guard inside the container blocks system-modifying commands. It
#: cannot see the conversation, so consent given in chat never reaches it — and it
#: must not, because the agent controls that conversation. This file is the only
#: consent channel: written as ROOT into /opt (which the agent user yolo cannot
#: write), it can only be created by the backend on an explicit user action.
WRITE_APPROVAL_FILE = "/opt/cs-write-approval.json"


def set_write_approval(user_id: str, minutes: int, granted_by: str) -> dict:
    """Open a time-limited write window in the user's container. Returns its state."""
    import json
    import time as _t
    from datetime import datetime, timezone, timedelta

    minutes = max(1, min(120, int(minutes)))
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = {
        "expires_at": _t.time() + minutes * 60,
        "expires_at_iso": expires.isoformat(timespec="seconds"),
        "granted_by": granted_by,
        "granted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    c = _client().containers.get(container_name(user_id))
    # user="0": root-owned so the agent (yolo) cannot forge or extend it.
    c.exec_run(
        ["sh", "-c", f"printf '%s' \"$A\" > {WRITE_APPROVAL_FILE} && "
                     f"chmod 644 {WRITE_APPROVAL_FILE}"],
        environment={"A": json.dumps(payload)},
        user="0",
    )
    log.info("write approval granted for %s by %s (%d min)", user_id, granted_by, minutes)
    return payload


def clear_write_approval(user_id: str) -> None:
    """Close the write window immediately."""
    c = _client().containers.get(container_name(user_id))
    c.exec_run(["sh", "-c", f"rm -f {WRITE_APPROVAL_FILE}"], user="0")
    log.info("write approval revoked for %s", user_id)


def get_write_approval(user_id: str) -> dict | None:
    """Return the active approval, or None when absent/expired."""
    import json
    import time as _t
    try:
        c = _client().containers.get(container_name(user_id))
        rr = c.exec_run(["sh", "-c", f"cat {WRITE_APPROVAL_FILE} 2>/dev/null || echo '{{}}'"])
        data = json.loads(rr.output.decode(errors="replace") or "{}")
        if float(data.get("expires_at", 0)) > _t.time():
            return data
    except Exception:
        pass
    return None
