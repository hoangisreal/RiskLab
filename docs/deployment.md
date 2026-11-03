# Deployment

RiskLab is a localhost demo. Prepare the Kaggle data under the competition rules; credentials remain outside git. No raw or processed data, predictions, generated reports or MLflow storage is distributed with the repository.

```bash
make setup
make test
kaggle competitions download -c home-credit-default-risk -p data/raw
unzip data/raw/home-credit-default-risk.zip -d data/raw
make ingest
make partition
mkdir -p reports features data/duckdb_tmp data/monitoring
docker compose build train api
docker compose up -d mlflow
python scripts/run_selection.py
```

The final command runs validation, Train features, E1/E2, repeated E1 on the served set, policy/leakage diagnostics, E3/E4, full candidate parity and Monitoring-only controls. Logs are retained under `reports/operations/`. Individual steps are available with `--steps`; use this to resume after resolving a failure. It never opens Test or changes registry aliases. It assumes raw inputs are already ingested/partitioned.

The train service uses the same image runtime and `uv.lock` as the API. Its repository mount is read-only at `/workspace`, with only reports/features, monitoring registrations and disk-backed DuckDB spill writable. Development/CI uses Python 3.12.14; the digest-pinned image uses 3.12.16. Keep training, preparation, locking and registration in the train image for exact compatibility.

Review and commit source/config/documentation changes before creating a deployment lock. Git must be clean. Generated artifacts stay ignored. Then:

```bash
docker compose run --rm train lock-selection --candidate reports/models/candidate.json
# Use the printed lock ID; registration leaves aliases unchanged.
docker compose run --rm train registry register --lock LOCK_ID
docker compose run --rm train registry promote --version VERSION --reason "validated Selection candidate"
# Set RISKLAB_ADMIN_API_KEY in the shell or ignored .env before starting the API.
docker compose up -d --build api
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/models/current
curl -X POST http://127.0.0.1:8000/predict -H 'Content-Type: application/json' \
  -d '{"contract_version":"1.1","application":{"AMT_CREDIT":500000,"AMT_INCOME_TOTAL":200000},"history":{"bureau":{"status":"provided","records":[]},"previous_applications":{"status":"provided","records":[]},"installments":{"status":"provided","records":[]}}}'
```

The sample supplies the selected contract-1.1 sources. Empty records explicitly assert no history; unavailable required sources return 422. See [API contract](api_contract.md) for types, bounds, references and explanations.

Registration stores the selected model, signature, pinned requirements, immutable reference profile, model card and validated lock. Promotion loads/verifies the bundle and applies the Selection gate against the current champion (absolute configured floors for a first version). Forced promotion requires a recorded reason and bypasses only metric checks. Runtime, source/schema and parity validation still apply.

## Test approval remains separate

Serving validation and reproducibility use Train/Calibration/Selection/Monitoring only. They do not require another Test opening. Contract-1.0's single historical Test report remains intact and does not evaluate the corrected artifact. Only after explicit owner authorization may the locked contract-1.1 candidate be evaluated:

```bash
# Do not run without explicit owner authorization.
docker compose run --rm train final-eval --lock LOCK_ID \
  --acknowledge-test-reuse "OWNER_APPROVED_REASON"
```

The completed local contract-1.1 evaluation used lock `402b9e050159762d` and run `a9fc07a3cbaf43ab88d450ed6a3eca58`, following explicit owner approval on 2026-11-04. It is opening two overall, one for that lock; this does not authorize a future new lock to open Test.

The durable journal records reuse before any read, including a failed/crashed evaluation. A completed lock returns its stored report without reopening Test. Never use that report to choose a model, feature, calibration method or threshold. Legacy artifacts are preserved, not relabeled as contract 1.1; the corrected contract requires its own Selection protocol and parity evidence.

## Reload, rollback and operations

```bash
curl -X POST http://127.0.0.1:8000/admin/models/reload -H "x-admin-key: $RISKLAB_ADMIN_API_KEY"
docker compose run --rm train registry rollback --version PREVIOUS_VERSION --reason "regression detected"
# Reload and inspect /models/current again.
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run python scripts/rollback_demo.py
docker compose run --rm train monitoring create --batch-id control-001 --scenario control
docker compose run --rm train monitoring evaluate --batch-id control-001
```

Reload resolves a numeric version, validates metadata/runtime/source/profile, downloads the artifact and swaps only after success. A failed load leaves the old model serving. Rollback requires a compatible, previously audited promotion and validates the target before changing the alias. All alias changes use the documented CLI and append to `reports/registry_audit.jsonl`.

Run the controlled rollback exercise with one API worker. It deliberately trains/registers a separate bad artifact, demonstrates gate blocking/forced promotion/detection, and restores the starting version even after an error. It never opens Test. Multiple-worker measurements use `RISKLAB_API_WORKERS`; process-local admin reload is not a broadcast deployment mechanism. See [Monitoring](monitoring.md) for controls, shared-drift limitations and load commands.

MLflow and the API are bound to localhost; MLflow is unauthenticated. Back up `mlflow-data/` while the server is stopped. Do not delete it to reset the demo. Verified registration, HTTP parity, monitoring, rollback and load evidence for the corrected artifact are recorded in [Progress](PROGRESS.md) and [Monitoring](monitoring.md). Operational proof identities describe those recorded runs; `/models/current` is authoritative for the active final revision's version, run and lock.
