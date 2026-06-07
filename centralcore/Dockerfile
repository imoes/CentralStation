FROM python:3.11-slim

# Proxy — passed as build args, exported for pip + git + apt
ARG HTTP_PROXY=http://proxy.ippen.media:80
ARG HTTPS_PROXY=http://proxy.ippen.media:80
ARG NO_PROXY=localhost,127.0.0.1,.ippen.media
ENV HTTP_PROXY=$HTTP_PROXY \
    HTTPS_PROXY=$HTTPS_PROXY \
    NO_PROXY=$NO_PROXY \
    http_proxy=$HTTP_PROXY \
    https_proxy=$HTTPS_PROXY \
    no_proxy=$NO_PROXY

# System tools: network diagnostics (terminal toolset) + git for clone
RUN apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" update \
    && apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" \
       install -y --no-install-recommends \
       git iputils-ping curl traceroute nmap dnsutils \
       netcat-openbsd mtr-tiny iperf3 jq wget iproute2 \
       openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

# Clone Hermes (shallow — latest main, no history needed)
# git respects http_proxy / https_proxy env vars set above
RUN git clone --depth=1 https://github.com/NousResearch/hermes-agent /opt/hermes

# Install Hermes + all its declared dependencies from pyproject.toml
RUN pip install --no-cache-dir /opt/hermes

# CentralCore-specific additions not in Hermes base deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# /opt/hermes makes run_agent.py and other top-level Hermes modules importable
ENV PYTHONPATH=/opt/hermes

ENTRYPOINT ["/entrypoint.sh"]
