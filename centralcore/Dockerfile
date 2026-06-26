FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg \
    iputils-ping traceroute nmap dnsutils \
    netcat-openbsd mtr-tiny iperf3 jq wget iproute2 \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 LTS (required by Hermes browser tools — pre-install to avoid runtime download)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

# Clone Hermes (shallow — latest main, no history needed)
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
