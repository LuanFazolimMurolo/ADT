FROM python:3.11-slim

WORKDIR /app

# Build the installed package only after all Hatchling inputs are present.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# Run with an unprivileged account; Phase 1 writes no local application data,
# but the configured data directory remains available for later worker phases.
RUN groupadd --system adt \
    && useradd --system --gid adt --home-dir /app --shell /usr/sbin/nologin adt \
    && mkdir -p /app/data \
    && chown -R adt:adt /app

# Container health means ready to receive traffic, including PostgreSQL.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import os, httpx; port = os.environ.get('ADT_API_PORT', '8000'); response = httpx.get('http://127.0.0.1:' + port + '/health/readiness', timeout=5); response.raise_for_status()"

EXPOSE 8000

USER adt

CMD ["python", "-m", "app.main"]
