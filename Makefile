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

.PHONY: app-init app-db app-migrate app-api app-worker app-web
app-init:
	uv run python scripts/init_application_dev.py

app-db: app-init
	docker compose -f deploy/compose.local.yml up -d --wait
	uv run python scripts/provision_application_dev.py

app-migrate:
	env -u RISKLAB_DATABASE_URL RISKLAB_DATABASE_URL_FILE=.secrets/local_owner_url uv run alembic upgrade head

app-api:
	env -u RISKLAB_DATABASE_URL RISKLAB_DATABASE_URL_FILE=.secrets/local_api_url uv run uvicorn risklab.business.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

app-worker:
	env -u RISKLAB_DATABASE_URL RISKLAB_DATABASE_URL_FILE=.secrets/local_worker_url uv run risklab-worker

app-web:
	cd apps/web && NEXT_TELEMETRY_DISABLED=1 npm run dev

.PHONY: app-prepare app-build app-start app-status app-stop
app-prepare:
	OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MLFLOW_DISABLE_AGENT_HINT=1 uv run python -m scripts.application_local prepare

app-build:
	cd apps/web && NEXT_TELEMETRY_DISABLED=1 RISKLAB_API_ORIGIN=http://127.0.0.1:8000 npm run build

app-start:
	uv run python -m scripts.application_local start

app-status:
	uv run python -m scripts.application_local status

app-stop:
	uv run python -m scripts.application_local stop

.PHONY: app-local
app-local:
	$(MAKE) app-db
	$(MAKE) app-migrate
	$(MAKE) app-prepare
	$(MAKE) app-build
	$(MAKE) app-start

# One-command Docker demo (synthetic data, loopback only). See README Quick Start.
# demo-up/down/logs/reset need only Docker + Make; demo-smoke also needs host Python.
.PHONY: demo-up demo-down demo-logs demo-smoke demo-reset
demo-up:
	docker compose -f deploy/compose.demo.yml up --build -d --wait --wait-timeout 600
	@echo "RiskLab demo ready at http://localhost:3000 (see README for demo accounts)"

demo-down:
	docker compose -f deploy/compose.demo.yml down

demo-logs:
	docker compose -f deploy/compose.demo.yml logs -f --tail=200

demo-smoke:
	uv run python scripts/docker_demo_smoke.py

demo-reset:
	docker compose -f deploy/compose.demo.yml down -v
