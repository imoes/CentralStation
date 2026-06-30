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
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates curl jq wget sudo \
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
RUN git clone --depth=1 https://github.com/NousResearch/hermes-agent /opt/hermes \
    && pip install --no-cache-dir /opt/hermes \
    && chmod -R a+rX /opt/hermes

WORKDIR /app
COPY userenv/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY userenv/main.py ./main.py
COPY userenv/hermes_config.yaml ./hermes_config.yaml
ENV PYTHONPATH=/opt/hermes

# ── Claude Code CLI (installed as yolo so it lands in /home/yolo/.local/bin) ──
USER yolo
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && /home/yolo/.local/bin/claude --version
USER root
# Symlink so all users (and exec_run) can reach the binary via PATH.
RUN ln -sf /home/yolo/.local/bin/claude /usr/local/bin/claude
ENV PATH=/home/yolo/.local/bin:$PATH \
    CLAUDE_CONFIG_DIR=/home/yolo/.claude

# ── Codex CLI (@openai/codex) — global so yolo can use it ────────────
RUN npm install -g @openai/codex

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

RUN touch /home/yolo/.bashrc && chown yolo:yolo /home/yolo/.bashrc

COPY userenv/entrypoint.sh /usr/local/bin/cs-entrypoint.sh
RUN chmod +x /usr/local/bin/cs-entrypoint.sh

USER yolo
ENTRYPOINT ["/usr/local/bin/cs-entrypoint.sh"]
