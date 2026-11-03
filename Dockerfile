# Multi-stage build from the same uv.lock; targets: train (CLI + MLflow) and api.
FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY configs/ configs/
COPY sql/ sql/

FROM base AS train
RUN uv sync --locked --no-dev
RUN useradd --create-home risklab && chown -R risklab /app
USER risklab
ENTRYPOINT ["uv", "run", "--no-sync", "risklab"]

FROM base AS api
RUN uv sync --locked --no-dev
RUN useradd --create-home risklab && chown -R risklab /app
USER risklab
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "risklab.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
