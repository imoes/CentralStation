# Prüf-Container: führt NUR Lese-Vorlagen aus cs_policy auf Servern aus, mit einem
# System-SSH-Account. Er nimmt keinen freien Befehlstext an — siehe cs_policy/templates.py.
FROM python:3.12-slim

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY} no_proxy=${NO_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}

# apt-get ignoriert http_proxy — es braucht Acquire::*::Proxy.
RUN if [ -n "$HTTP_PROXY" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' \
            "$HTTP_PROXY" "${HTTPS_PROXY:-$HTTP_PROXY}" > /etc/apt/apt.conf.d/01proxy; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/01proxy

RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn==0.32.1"

WORKDIR /app
COPY cs_policy/ ./cs_policy/
COPY probe/main.py ./main.py

# Laufzeit ohne Build-Proxy: der Runner spricht nur SSH (über den Sprung-Host).
ENV http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY=
EXPOSE 8090
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
