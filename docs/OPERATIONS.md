# RiskLab — Operations

**Scope: existing local ML/scoring stack.** These commands do not deploy the
authenticated business application. For the local application see the
[project README](../README.md); current portfolio checks are recorded in
[verification](portfolio/verification.md). Local deployment tooling exists;
VPS deployment and off-VPS recovery remain unverified.
The current localhost API/MLflow configuration must not be exposed as a public
demo without the new authorization and infrastructure controls.

Procedures for setup, selection, locking, serving, monitoring, and
reproduction. Architecture: `docs/PROJECT.md`. Frozen values:
`docs/DECISIONS.md`. Never open Test from anything in this file except the
gated final-eval step.

## 1. Setup and full Selection protocol

```bash
make setup
make test                       # synthetic only; no Kaggle credentials needed
# After accepting Kaggle rules and placing CSVs in data/raw:
make ingest
make partition
mkdir -p reports features data/duckdb_tmp data/monitoring
docker compose build train api
docker compose up -d mlflow     # MLFLOW_TRACKING_URI=http://127.0.0.1:5000
python scripts/run_selection.py # validation -> features -> E1/E2/E1-served
                                # -> policy/diagnostics -> E3/E4 -> candidate
                                # parity -> Monitoring controls
```

`scripts/run_selection.py --steps STEP ...` resumes individual stages; logs
under `reports/operations/`. It never opens Test or moves aliases. Keep
training/locking/registration in the train image: dev/CI is Python 3.12.14,
the digest-pinned image is 3.12.16; both resolve the same `uv.lock`.

## 2. Lock, register, promote, serve

Review and commit source/config/docs first — git must be clean (generated
artifacts stay ignored). Registration leaves aliases unchanged.

```bash
docker compose run --rm train lock-selection --candidate reports/models/candidate.json
docker compose run --rm train registry register --lock LOCK_ID
docker compose run --rm train registry promote --version VERSION --reason "validated Selection candidate"
# Set RISKLAB_ADMIN_API_KEY in the shell or ignored .env first.
docker compose up -d --build api
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/models/current
```

Promotion loads/verifies the bundle and applies the Selection gate against
the current champion (configured absolute floors for a first version).
Forced promotion requires a recorded reason and bypasses only metric checks
— runtime, source/schema, and parity validation still apply. Registration
stores the model, signature, pinned requirements, immutable reference
profile, model card, and validated lock; the loader re-verifies profile
bytes, feature list, model hash, contract, and Selection aggregates before
any swap.

## 3. API (contract 1.1 summary; full contract: `docs/api_contract.md`)

| Endpoint | Behavior |
|---|---|
| `POST /predict` | Shared cleaning + feature SQL, frozen threshold, `approve`/`review`, versions, warnings, optional explanation |
| `GET /models/current` | Pinned identity, hashes, schema/protocol provenance, frozen costs, Selection metrics, matching cached Test aggregates (or `null`) |
| `GET /health` | Liveness + model readiness |
| `POST /monitoring/evaluate` | Registered `batch_id`, optional pinned version; unknown ID → 404 |
| `POST /admin/models/reload` | Admin key from env; validate/download before swap; failure keeps current model (502) |

Serving rules: required history source `not_provided`/absent
→ 422 (never impute as no-history); `provided` + empty records asserts no
history; raw IDs, `TARGET`, `CODE_GENDER`, computed ratios/flags are
forbidden in requests; body caps 5,000 records/source and 5,000,000 bytes
(413); logs carry request ID/version/status/latency — never payloads or keys.
Reload is process-local: run 1 worker (`RISKLAB_API_WORKERS=1`) unless doing
the load matrix.

## 4. Final evaluation (gated, separate)

Only after explicit owner authorization for a concrete clean-tree lock:

```bash
# Do not run without explicit owner authorization.
docker compose run --rm train final-eval --lock LOCK_ID \
  --acknowledge-test-reuse "OWNER_APPROVED_REASON"
```

The journal records the opening **before** any read (including a
failed/crashed attempt). A completed lock returns its stored report without
reopening. Never use the report to choose model/features/calibration/
threshold. Completed instances: lock `402b9e050159762d`, run
`a9fc07a3cbaf43ab88d450ed6a3eca58` (D18), and the re-lock
`17b83143e33bb907`, run `50d36f3b142a41039ff4d72967242f9a` (D20) — those
approvals do not extend to any future lock.

## 5. Monitoring, rollback, load

```bash
docker compose run --rm train monitoring create --batch-id control-001 --scenario control
docker compose run --rm train monitoring evaluate --batch-id control-001
docker compose run --rm train monitoring calibrate --candidate reports/models/candidate.json
uv run python scripts/rollback_demo.py   # 1 API worker; gate block -> force -> detect -> restore
uv run python scripts/load_test.py --workers 1   # and --workers 4 for the matrix
curl -X POST http://127.0.0.1:8000/admin/models/reload -H "x-admin-key: $RISKLAB_ADMIN_API_KEY"
docker compose run --rm train registry rollback --version PREVIOUS_VERSION --reason "regression detected"
```

- Scenarios: `control`, `covariate_young`, `covariate_young_mild`,
  `bug_income_x1000`, `bug_ext3_missing`, `bug_category_renamed`,
  `label_flip`, `kaggle_unlabeled` — strengths/sizes in
  `configs/monitoring.yaml`. The API takes a registered `batch_id`, never a
  file path. Kaggle unlabeled reports have drift/score only, no invented
  performance.
- Alert policy: smallest of scales [1, 1.25, 1.5, 2] meeting the 5%
  calibration target on 100 calibration controls, frozen before 100 held-out
  controls are measured (accepted: scale 1.0, run
  `dd00fae6c1ad4f3baec4e1c594f867b8`). Batches share a static pool and may
  overlap. Mild age shift is missed at these thresholds (disclosed).
- Rollback requires a compatible previously-audited promotion; every alias
  change appends to `reports/registry_audit.jsonl`. Rollback fixes a model
  regression, not shared data/concept drift (both versions degraded in the
  exercise, run `1f82bddd7d374a7181a57445f65c9bc9`).
- Load: 200 measured requests/case, concurrency 8, 1/4 workers (runs in
  `docs/RESULTS.md` §7). The 100 ms p95 target is unmet; no SLO is claimed.
  Stage profile (host, single request, representative Train payload,
  no explanation, 30 repeats): `prepare_input` ≈ 79 ms median of which
  online DuckDB feature SQL ≈ 62 ms (group SQL ≈ 42 ms, history views
  ≈ 7 ms, connection ≈ 1 ms, rest cleaning/assembly), `predict_proba`
  ≈ 19 ms, total ≈ 99 ms. Empty history costs the same — the bottleneck is
  per-request DuckDB planning/setup, not history size, and it cannot be
  removed without breaking batch/online SQL parity. SQL texts are cached
  per process (`features/build.py`); concurrency latency is CPU contention
  (4 workers: p95 351 ms vs 859 ms at 1 worker in the recorded matrix).

## 6. Verify and reproduce (never opens Test, never moves aliases)

```bash
uv run risklab verify --reference reports/reference/contract-1.1/
make reproduce OUT=reports/workspaces/reproduction   # OUT must be absent/empty, disk-backed
uv run python scripts/recompute_metrics.py reports/experiments/e1  # plain-sklearn check
```

- `reports/reference/*.json` = contract-1.0 baseline; `contract-1.1/` =
  corrected baseline. Both hold hashes/counts/Selection aggregates with run
  IDs — never rows, predictions, models, or Test results. Do not overwrite a
  failing historical reference to make verification pass.
- Reproduction copies configs/SQL, redirects derived outputs, runs
  ingest/validation/features, E1/E2/E1-served, policy/diagnostics, E3/E4,
  candidate verification. Parquet bytes vary; equality is by
  key/value/missing-mask (`--feature-reference DIR`, tolerance 1e-9).
  Same-image metric tolerance 1e-6. Accepted run:
  `c3a42f2254144abf9d470f3495e13345` (identical artifact hash).
- `risklab snapshot-reference --out DIR` exports a new aggregate baseline
  from an explicitly chosen run only.

## 7. Local state and safety

- Ignored, never committed: `data/`, `features/`, `mlflow-data/`, `reports/*`
  except `reports/reference/`. Back up `mlflow-data/` only while the server
  is stopped. **Ask before** deleting data/`mlflow-data/`, rewriting git
  history, changing partition seed/shares, or moving aliases outside the CLI.
- MLflow is localhost + unauthenticated; API is localhost demo. The admin key
  comes from `RISKLAB_ADMIN_API_KEY`; Kaggle credentials stay in `~/.kaggle/`
  or env. Secrets never enter code, configs, logs, or MLflow.
