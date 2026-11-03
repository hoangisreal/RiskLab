.PHONY: setup lint format typecheck test ingest partition validate synthetic mlflow e1 diagnostics features e2 e3 e4 reproduce

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
	OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MLFLOW_DISABLE_AGENT_HINT=1 uv run pytest -m "not data"

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

features:
	uv run risklab features

e2:
	uv run risklab e2

e3:
	uv run risklab e3

e4:
	uv run risklab e4

# Use an isolated output root: make reproduce OUT=/tmp/risklab-reproduce
reproduce:
	@test -n "$(OUT)" || (echo 'Set OUT to an absent or empty output directory'; exit 1)
	uv run python scripts/reproduce.py --out "$(OUT)"
