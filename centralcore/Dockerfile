FROM python:3.11-slim

# Proxy build args — pip uses these automatically
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=$HTTP_PROXY \
    HTTPS_PROXY=$HTTPS_PROXY \
    NO_PROXY=$NO_PROXY

# Network diagnostic tools needed by Hermes terminal toolset
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping curl traceroute nmap dnsutils \
    netcat-openbsd mtr-tiny iperf3 jq wget iproute2 \
    && rm -rf /var/lib/apt/lists/*

# ── Install Hermes from the 'hermes' additional build context ──────
# The hermes build context is /home/mutkluge/Dev/code/hermes-agent
# passed via docker-compose additional_contexts.
COPY --from=hermes . /opt/hermes/
RUN pip install --no-cache-dir /opt/hermes

# ── CentralCore additions (not in Hermes base deps) ───────────────
# faster-whisper is Hermes optional[voice]; fastapi/uvicorn are the wrapper
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# /opt/hermes makes run_agent.py and other top-level modules importable
ENV PYTHONPATH=/opt/hermes

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
