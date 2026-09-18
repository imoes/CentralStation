# Per-user unified Werkbank+Hermes image.
# Runs both code-server (VS Code in the browser, port 8080) and Hermes
# (FastAPI, port 8001) in the same container so they share ~/.ssh and ~/workspaces.
#
# Base: codercom/code-server:latest — ships /usr/lib/code-server/lib/node needed by
# the CSP/OAuth entrypoint patches.  Python 3.11 and Hermes are added on top.
#
# Runtime user: yolo (UID 1000) — non-root so Claude Code's
# --dangerously-skip-permissions is available (blocked for root since 2.1.195).
#
# Build:  docker compose build userenv   (tags centralstation-userenv:latest)
FROM codercom/code-server:latest

USER root

# Proxy build-args (empty by default — supplied via docker-compose.override.yml).
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""

# Cache-Bruch für die Agenten-CLIs. Hermes, Claude Code, Codex und Playwright-MCP
# sind bewusst NICHT auf eine Version gepinnt (git --depth=1 bzw. @latest) — ohne
# diesen ARG liefert der Docker-Layer-Cache aber trotzdem dieselben alten Stände.
# Neue Versionen holen:
#   docker compose build --build-arg AGENT_REFRESH=$(date +%s) userenv
ARG AGENT_REFRESH=""

ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}

# ── APT proxy ─────────────────────────────────────────────────────────
# apt-get ignores http_proxy/HTTP_PROXY env vars — it needs Acquire::*::Proxy.
# Without this, every apt layer bypasses the proxy and hangs/times out.
RUN if [ -n "$HTTP_PROXY" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' \
            "$HTTP_PROXY" "${HTTPS_PROXY:-$HTTP_PROXY}" \
            > /etc/apt/apt.conf.d/01proxy; \
    fi

# ── System tools ──────────────────────────────────────────────────────
# Includes network diagnostics (Hermes SSH/ping), python3 (Ansible+pip), git.
# sshpass: password auth for hosts where the marvin key is not deployed. The
# read-only guard already counts sshpass as a remote command, so a write wrapped in
# it still needs an explicit approval window.
# rsync: moving files between servers. scp/sftp come with openssh-client; rsync is a
# separate package and was missing. Note it must exist on BOTH ends of a transfer —
# it is not a protocol the remote sshd speaks by itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git openssh-client sshpass rsync ca-certificates curl jq wget sudo \
        python3 python3-pip python3-venv \
        iputils-ping traceroute nmap dnsutils netcat-openbsd mtr-tiny iproute2 \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS (required by Hermes browser tools) ────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | \
    https_proxy=${HTTPS_PROXY} http_proxy=${HTTP_PROXY} bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Runtime user: yolo (UID 1000) ────────────────────────────────────
# The base image ships user "coder" at UID 1000; we rename it to "yolo" so
# claude --dangerously-skip-permissions works (blocked for root ≥2.1.195).
# Passwordless sudo retained for maintenance tasks inside the container.
RUN usermod -l yolo coder \
    && usermod -d /home/yolo -m yolo \
    && groupmod -n yolo coder \
    && echo 'yolo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# ── Python venv (avoids Debian PEP 668 protection + RECORD-less system packages) ─
# World-readable so yolo can use it.
RUN python3 -m venv /opt/venv \
    && chmod -R a+rX /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv

# ── Ansible (for Werkbank terminal use) ──────────────────────────────
RUN pip install --no-cache-dir ansible ansible-lint

# ── Hermes AI agent ───────────────────────────────────────────────────
# Editable install (-e) is REQUIRED: upstream's build backend deliberately refuses to
# build wheels/sdists ("Building wheels or sdists for hermes-agent is not supported",
# it targets uv/uv2nix) and names `uv pip install -e .` as the supported path. A plain
# `pip install /opt/hermes` fails the build. This only surfaced once the moving
# code-server:latest base got a new digest and invalidated this layer's cache.
# -e keeps the source at /opt/hermes on sys.path, which the image retains anyway
# (Hermes modules are also imported via PYTHONPATH=/opt/hermes).
RUN echo "agent-refresh=${AGENT_REFRESH}" \
    && git clone --depth=1 https://github.com/NousResearch/hermes-agent /opt/hermes \
    && pip install --no-cache-dir -e /opt/hermes \
    && chmod -R a+rX /opt/hermes

WORKDIR /app
COPY userenv/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/opt/hermes
# NB: main.py, hermes_config.yaml and cs-readonly-guard.py are copied at the very
# END of this file on purpose — see the note there.

# ── Claude Code CLI (installed as yolo so it lands in /home/yolo/.local/bin) ──
USER yolo
RUN echo "agent-refresh=${AGENT_REFRESH}" \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && /home/yolo/.local/bin/claude --version
USER root
# Symlink so all users (and exec_run) can reach the binary via PATH.
RUN ln -sf /home/yolo/.local/bin/claude /usr/local/bin/claude
ENV PATH=/home/yolo/.local/bin:$PATH \
    CLAUDE_CONFIG_DIR=/home/yolo/.claude

# ── Codex CLI (@openai/codex) — global so yolo can use it ────────────
RUN echo "agent-refresh=${AGENT_REFRESH}" \
    && npm install -g @openai/codex \
    && codex --version

# ── Playwright MCP (browser automation for the Computer Console) ─────
# Shared stdio MCP server usable by all three Console agents (Hermes,
# Claude CLI, Codex CLI). Installed globally → the `playwright-mcp` binary
# lands on PATH. The Chromium browser lives in a world-readable path
# (PLAYWRIGHT_BROWSERS_PATH) so the non-root yolo user can drive it. The
# server is spawned with --browser chromium --headless --no-sandbox
# (container has no user-namespace sandbox), see userenv_manager.py.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN echo "agent-refresh=${AGENT_REFRESH}" \
    && npm install -g @playwright/mcp@latest \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
       https_proxy=${HTTPS_PROXY} http_proxy=${HTTP_PROXY} \
       npx playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright \
    # @playwright/mcp's `--browser chromium` maps to the "chrome-for-testing"
    # channel (not installed). Pin a stable symlink to the Chromium we did
    # download and pass it via --executable-path (see userenv_manager.py); this
    # survives Playwright browser-revision bumps that change the chromium-NNNN dir.
    && ln -sf "$(find /opt/ms-playwright -type f -path '*chrome-linux64/chrome' | head -1)" \
              /opt/ms-playwright/chrome-stable

# ── VS Code extensions baked into image ──────────────────────────────
# Installed to /opt/cs-extensions (not into $HOME which is per-user bind mount).
# The entrypoint seeds them into each user's extensions dir on first start.
RUN code-server \
        --extensions-dir /opt/cs-extensions \
        --install-extension Anthropic.claude-code \
        --install-extension openai.chatgpt \
    && chmod -R a+rX /opt/cs-extensions \
    && ls -1 /opt/cs-extensions

# ── Werkbank File Manager extension (pure JS, no build step) ─────────────────
# Pre-seeded alongside the other bundled extensions; entrypoint seeds on start.
COPY userenv/cs-filemanager /opt/cs-extensions/cs-filemanager-0.0.1/
RUN chmod -R a+rX /opt/cs-extensions/cs-filemanager-0.0.1/

# ── Navigator shim (build-time, needs root write access to /usr/lib) ──
# Patched at image build so the entrypoint (running as yolo) doesn't need
# root to modify /usr/lib/code-server. Only applied if pattern is still present.
RUN _ep="/usr/lib/code-server/lib/vscode/out/vs/workbench/api/node/extensionHostProcess.js"; \
    if grep -qF 'vscode-extensions/navigator' "$_ep" && \
       ! grep -qF 'userAgent:"node"' "$_ep"; then \
        sed -i \
            's|get:()=>{ea(new Zs("navigator is now a global in nodejs, please see https://aka.ms/vscode-extensions/navigator for additional info on this error."))}|get:()=>({userAgent:"node",platform:process.platform,language:"en-US",languages:["en-US"],onLine:!0,hardwareConcurrency:2,cookieEnabled:!1,appName:"Netscape",appVersion:"5.0",product:"Gecko"})|g' \
            "$_ep" && echo "Dockerfile: patched navigator shim"; \
    fi; \
    true

# ── Persistent pip user packages ──────────────────────────────────────
# A user virtualenv lives at /home/yolo/pip/venv (on volume cs-pip-<uid>).
# The entrypoint creates it on first start if it doesn't exist yet.
# PATH is prepended so the user venv wins over the system /opt/venv.
ENV PATH=/home/yolo/pip/venv/bin:$PATH
RUN mkdir -p /home/yolo/pip && chown yolo:yolo /home/yolo/pip
# ENV only covers non-login shells. A login shell (`bash -lc`, which is how some
# agents invoke commands) sources /etc/profile, which RESETS PATH — the user venv
# then vanishes and `pip install` silently hits the system Python instead. The
# profile.d snippet runs after that reset and puts it back.
RUN printf '%s\n' \
        '# CentralStation: keep the per-user pip venv ahead of the system Python,' \
        '# also in login shells where /etc/profile has just reset PATH.' \
        'case ":$PATH:" in' \
        '  *":/home/yolo/pip/venv/bin:"*) ;;' \
        '  *) PATH="/home/yolo/pip/venv/bin:/home/yolo/.local/bin:/opt/venv/bin:$PATH" ;;' \
        'esac' \
        'export PATH' \
        > /etc/profile.d/10-cs-path.sh \
    && chmod 644 /etc/profile.d/10-cs-path.sh

RUN touch /home/yolo/.bashrc && chown yolo:yolo /home/yolo/.bashrc

# ── Our own source files, last ────────────────────────────────────────
# These four change on nearly every commit; everything above them changes
# rarely. Docker invalidates every layer BELOW a changed one, so while these
# sat further up, editing one line of main.py threw away the Claude CLI
# install, the Codex install, the ~150 MB Chromium download and the VS Code
# extensions — a full rebuild for a two-line edit. Keep them at the bottom.
WORKDIR /app
COPY userenv/main.py ./main.py
COPY userenv/hermes_config.yaml ./hermes_config.yaml
# Read-only guard for Claude CLI (PreToolUse hook — blocks system-modifying Bash).
COPY userenv/cs-readonly-guard.py /opt/cs-readonly-guard.py
RUN chmod a+rx /opt/cs-readonly-guard.py

COPY userenv/entrypoint.sh /usr/local/bin/cs-entrypoint.sh
RUN chmod +x /usr/local/bin/cs-entrypoint.sh

USER yolo
ENTRYPOINT ["/usr/local/bin/cs-entrypoint.sh"]
