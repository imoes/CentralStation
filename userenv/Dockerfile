# Per-user unified Werkbank+Hermes image.
# Runs both code-server (VS Code in the browser, port 8080) and Hermes/CentralCore
# (FastAPI, port 8001) in the same container so they share /root/.ssh and /root/workspaces.
#
# Base: codercom/code-server:latest — ships /usr/lib/code-server/lib/node needed by
# the CSP/OAuth entrypoint patches.  Python 3.11 and Hermes are added on top.
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

# ── System tools ──────────────────────────────────────────────────────
# Includes network diagnostics (Hermes SSH/ping), python3 (Ansible+pip), git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates curl jq wget \
        python3 python3-pip python3-venv \
        iputils-ping traceroute nmap dnsutils netcat-openbsd mtr-tiny iproute2 \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS (required by Hermes browser tools) ────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | \
    https_proxy=${HTTPS_PROXY} http_proxy=${HTTP_PROXY} bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python venv (avoids Debian PEP 668 protection + RECORD-less system packages) ─
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv

# ── Ansible (for Werkbank terminal use) ──────────────────────────────
RUN pip install --no-cache-dir ansible ansible-lint

# ── Hermes AI agent ───────────────────────────────────────────────────
RUN git clone --depth=1 https://github.com/NousResearch/hermes-agent /opt/hermes \
    && pip install --no-cache-dir /opt/hermes

WORKDIR /app
COPY centralcore/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY centralcore/main.py ./main.py
ENV PYTHONPATH=/opt/hermes

# ── Claude Code CLI ───────────────────────────────────────────────────
ENV PATH=/root/.local/bin:$PATH
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && (ln -sf /root/.local/bin/claude /usr/local/bin/claude 2>/dev/null || true) \
    && claude --version
ENV CLAUDE_CONFIG_DIR=/root/.claude

# ── VS Code extensions baked into image ──────────────────────────────
# Installed to /opt/cs-extensions (not into /root/.local which is per-user bind mount).
# The entrypoint seeds them into each user's extensions dir on first start.
RUN code-server \
        --extensions-dir /opt/cs-extensions \
        --install-extension Anthropic.claude-code \
        --install-extension openai.chatgpt \
    && ls -1 /opt/cs-extensions

RUN touch /root/.bashrc

COPY userenv/entrypoint.sh /usr/local/bin/cs-entrypoint.sh
RUN chmod +x /usr/local/bin/cs-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/cs-entrypoint.sh"]
