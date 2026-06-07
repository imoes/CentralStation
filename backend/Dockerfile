FROM python:3.12-slim

RUN apt-get -o Acquire::http::Proxy="http://proxy.ippen.media:80" -o Acquire::https::Proxy="http://proxy.ippen.media:80" update && apt-get -o Acquire::http::Proxy="http://proxy.ippen.media:80" -o Acquire::https::Proxy="http://proxy.ippen.media:80" install -y --no-install-recommends \
    gcc libpq-dev libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --proxy http://proxy.ippen.media:80 -r requirements.txt

# Copy application code and documentation
COPY backend/ .
COPY README.md .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
