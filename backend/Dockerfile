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
# Gemeinsame Regeln für Befehle auf Servern (auch im Prüf-Container und in der Konsole).
COPY cs_policy/ ./cs_policy/
# Hook der Konsole (vi_konsole.py) direkt aus dem vi-mcp-Paket, das auch die lokale VI baut — keine Kopie im Repo,
# er zieht bei jedem Update (vi-mcp/aktualisieren.sh) mit. Ohne Paket bricht der Bau ab, statt still ohne Hook zu laufen.
COPY vi-mcp/pakete/ /tmp/vi-pakete/
RUN set -e; deb=$(ls /tmp/vi-pakete/vi-mcp_*_all.deb | sort -V | tail -1); \
    mkdir -p app/resources; \
    dpkg-deb --fsys-tarfile "$deb" | tar -xO ./usr/share/vi-mcp/hook/vi_hook.py > app/resources/vi_hook.py; \
    test -s app/resources/vi_hook.py; rm -rf /tmp/vi-pakete

CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
