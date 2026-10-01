FROM python:3.12-slim

# Proxy build-args (empty by default — supplied via docker-compose.override.yml).
# apt-get and pip honor the http_proxy/https_proxy env vars automatically.
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# drain3 0.9.11 pinnt cachetools==4.2.1 und jsonpickle==1.5.1 — beide längst überholt und
# unvereinbar mit fastmcp (cachetools >= 5). Es läuft mit den aktuellen Versionen
# (geprüft: tests/test_template_miner.py inkl. Zustand speichern/laden), daher ohne
# seine starren Pins installieren.
RUN pip install --no-cache-dir --no-deps drain3==0.9.11

# Copy application code and documentation
COPY backend/ .
COPY README.md .

CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
