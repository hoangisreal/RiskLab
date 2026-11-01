.PHONY: setup lint format typecheck test ingest synthetic

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

synthetic:
	uv run risklab synthetic
