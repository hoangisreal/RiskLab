.PHONY: setup lint format typecheck test ingest partition validate synthetic mlflow e1 diagnostics

setup:
	uv sync --locked

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy src

test: lint typecheck
	uv run pytest -m "not data"

ingest:
	uv run risklab ingest

partition:
	uv run risklab partition

validate:
	uv run risklab validate

synthetic:
	uv run risklab synthetic

# Local tracking server: SQLite backend and proxied artifacts under mlflow-data/ (git-ignored).
# Clients use MLFLOW_TRACKING_URI=http://127.0.0.1:5000 and never open the database directly.
mlflow:
	mkdir -p mlflow-data/artifacts
	MLFLOW_DISABLE_AGENT_HINT=1 uv run mlflow server --host 127.0.0.1 --port 5000 \
		--backend-store-uri sqlite:///mlflow-data/mlflow.db \
		--artifacts-destination ./mlflow-data/artifacts

e1:
	uv run risklab e1

diagnostics:
	uv run risklab diagnostics
