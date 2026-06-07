FROM python:3.12-slim AS builder

ARG HTTP_PROXY=http://proxy.ippen.media:80
ARG HTTPS_PROXY=http://proxy.ippen.media:80
ARG NO_PROXY=localhost,127.0.0.1,.ippen.media

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV NO_PROXY=${NO_PROXY}

WORKDIR /app

RUN apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" update \
    && apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" \
       install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime image (no build tools, smaller) ──────────────────────────────────
FROM python:3.12-slim

ARG HTTP_PROXY=http://proxy.ippen.media:80
ARG HTTPS_PROXY=http://proxy.ippen.media:80
ARG NO_PROXY=localhost,127.0.0.1,.ippen.media

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV NO_PROXY=${NO_PROXY}

RUN apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" update \
    && apt-get -o Acquire::http::Proxy="${HTTP_PROXY}" -o Acquire::https::Proxy="${HTTPS_PROXY}" \
       install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy application code and documentation
COPY backend/ .
COPY README.md .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
