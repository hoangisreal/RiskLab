# Frozen base/tool digests were resolved by the verified compose build on 2026-11-03.
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS base
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src/ src/
COPY configs/ configs/
COPY sql/ sql/
RUN uv sync --locked --no-dev && useradd --create-home risklab \
    && mkdir -p data/duckdb_tmp reports features \
    && chown -R risklab data reports features

FROM base AS train
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
USER risklab
ENTRYPOINT ["/app/.venv/bin/risklab"]

FROM base AS api
USER risklab
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "risklab.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Business application image: same codebase, different entrypoints (B06-T01).
FROM base AS business
COPY migrations/ migrations/
COPY alembic.ini ./
USER risklab
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live')"]
CMD ["/app/.venv/bin/uvicorn", "risklab.business.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM business AS worker
CMD ["/app/.venv/bin/risklab-worker"]

FROM business AS migrate
CMD ["/app/.venv/bin/alembic", "upgrade", "head"]
