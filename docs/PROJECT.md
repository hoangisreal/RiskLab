# RiskLab — Current ML Architecture

This document describes the **implemented ML/scoring foundation**. The business
application (import, worker, review and decision) is documented in
[architecture/overview.md](architecture/overview.md) and the
[local application guide](development/application.md). Current verification and
remaining deployment limits live in [PROGRESS.md](PROGRESS.md).
See [README.md](README.md) for the documentation reading guide.

RiskLab estimates the probability that a loan applicant will have payment
difficulties (`TARGET`) on the Home Credit Default Risk dataset. It compares
Logistic Regression and LightGBM, selects historical feature sources,
calibrates, picks a decision threshold, serves the frozen artifact through
FastAPI, and demonstrates monitoring and audited rollback.

RiskLab is a production-oriented demo, not a production credit-decisioning
system. The `approve`/`review` output is illustrative. Limitations are listed
in `docs/RESULTS.md` §8.

New human-facing guides are Vietnamese; code, technical comments, docstrings and
agent instructions are English. The portfolio README and historical ML evidence
remain English (D24 in [DECISIONS.md](DECISIONS.md)).

## Frozen state

| Item | Value |
|---|---|
| Feature contract | 1.1 (contract-1.0 evidence is historical only) |
| Served sources | application (+5 row-wise ratios), bureau, previous, installments |
| Model inputs | 147 columns |
| Model family | LightGBM (repeated E1 on served set), calibration: none |
| Artifact SHA256 | `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676` |
| Base model run | `4e46b91eca76426396e82828fa7b3b1e` |
| E3 run / E4 run | `e1f58f0e2ad14e74980b7ac5a855191b` / `82f54a4c01fe4f90829fceeaef60a269` |
| Threshold | `0.08040201` (per-loan proxy: FP = 0.05×credit, FN = 0.50×credit) |
| Final lock | `17b83143e33bb907` (served as registry v7) |
| Final Test run | `50d36f3b142a41039ff4d72967242f9a`, lock `17b83143e33bb907` |
| Partition | seed `20261101`, shares Train/Cal/Sel/Test/Mon = 55/10/10/15/10 |
| Partition hash | `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101` |

Every reported metric carries its MLflow run ID, plus the lock ID for Test
metrics. Full numbers are in `docs/RESULTS.md` and `docs/model_card.md`.
Design decisions and their rationale are recorded in `docs/DECISIONS.md`.

## Architecture and data flow

```
Kaggle CSV (data/raw, never committed)
  -> risklab ingest        Parquet + data/manifest.json (SHA256, row counts)
  -> risklab validate      SQL checks in DuckDB over Parquet -> reports/validation/
  -> risklab partition     stratified IDs + partition_hash -> data/partitions/
  -> role-gated loaders    src/risklab/data/loaders.py (Test always refused)
  -> sql/features/*.sql    DuckDB aggregates, applicant-local only, no TARGET
  -> sklearn Pipeline      preprocessing fitted on Train only -> LR | LightGBM
  -> Calibration partition FrozenEstimator calibrators (contract 1.1: none chosen)
  -> Selection partition   family / sources / calibration / threshold + gate
  -> selection lock        models/select.py freezes artifact+config/SQL hashes
  -> final-eval            models/final_eval.py, the only Test reader
  -> MLflow registry       alias @champion, immutable serving bundle
  -> FastAPI               /predict /models/current /health /monitoring/evaluate
  -> Monitoring pool       seeded drift/performance simulations, never trains
```

### Design invariants

These invariants are enforced in code and covered by tests:

1. Partition roles: Train fits models, Calibration fits calibrators only,
   Selection decides model/sources/calibration/threshold and the promotion
   gate, Test is read only by the locked final evaluation, and the Monitoring
   pool is used only for simulations.
2. Labeled rows are read only through `risklab.data.loaders.load_partition`,
   which never returns Test. Test is read only in
   `src/risklab/models/final_eval.py`.
3. Any statistic computed across applicants (imputation, scaling, encoding,
   feature selection) is fitted inside the sklearn Pipeline on Train only.
   Feature SQL aggregates only an applicant's own history and never
   references `TARGET`. Historical events must precede the application.
4. No class weighting or resampling in candidate models unless an experiment
   config explicitly asks for it.
5. The served artifact is the evaluated artifact. There is no refit after
   selection.
6. Batch and online (API) features share the same cleaning code and the same
   SQL files. Any change to either requires the parity test to pass.

## Module map (`src/risklab/`)

| Package / file | Responsibility |
|---|---|
| `cli.py` | Typer CLI for all `risklab *` commands |
| `config.py` | Pydantic YAML config loading and validation |
| `manifest.py`, `tracking.py` | Run manifests; MLflow run setup and provenance tags |
| `verify.py` | `risklab verify` against `reports/reference/` aggregates |
| `synthetic.py` | Synthetic raw-data generator used by tests and CI |
| `data/ingest.py` | CSV to Parquet conversion with explicit types and manifest |
| `data/partition.py` | Deterministic stratified assignment (BLAKE2b seed:id) |
| `data/loaders.py` | Role-gated `load_partition` with hash re-verification |
| `data/clean.py` | Row-wise `clean_application` from `configs/cleaning.yaml` |
| `data/validate.py`, `validate_history.py` | SQL validation against the data contract |
| `data/duckdb_utils.py` | Bounded-memory DuckDB helpers |
| `features/registry.py` | Feature-group registry from `configs/features.yaml` |
| `features/application.py`, `historical.py`, `history_clean.py` | Application ratios, history feature frames, event cleaning |
| `features/build.py` | Batch feature build to `features/*.parquet` |
| `features/online.py`, `payload.py`, `parity.py` | Per-request features, API payload conversion, batch/API parity |
| `experiments/e1.py` | E1: Logistic Regression vs LightGBM (plus `--served` repeat) |
| `experiments/e234.py` | E2 sources, E3 calibration, E4 threshold runners |
| `experiments/e5.py` | E5 post-lock challenger study (XGBoost/CatBoost/HistGB vs champion), config in `experiments/e5.yaml` outside the protocol-hashed tree |
| `experiments/diagnostics.py`, `models/diagnostics.py` | Leakage diagnostics (permutation, single-feature screening) |
| `models/pipelines.py` | Unfitted LR/LightGBM sklearn Pipelines |
| `models/calibrate.py`, `threshold.py` | FrozenEstimator calibration; cost and threshold analysis |
| `models/evaluate.py` | Metrics, paired bootstrap, prediction files |
| `models/select.py` | Selection lock (content-bound, clean-tree) |
| `models/final_eval.py` | The only Test reader; evaluation ledger and reuse acknowledgment |
| `models/candidate.py`, `demo_candidate.py` | Candidate verification without refit; rollback-demo artifact |
| `models/explain.py` | TreeSHAP / LR contributions grouped to raw inputs |
| `registry/bundle.py`, `register.py`, `gates.py`, `client.py`, `audit.py` | Serving bundle, registration, promotion gate, alias operations, audit log |
| `api/main.py`, `service.py`, `model_store.py` | FastAPI app, prediction service, pinned model loading |
| `api/schemas.py`, `contract.py`, `validation.py` | Request/response contract 1.1 and relational validation |
| `api/config.py`, `context.py`, `metadata.py`, `middleware.py` | API settings, request context, `/models/current`, payload-free logging |
| `monitoring/profile.py`, `simulate.py` | Train-fitted reference profile; seeded batch simulator |
| `monitoring/drift.py`, `performance.py`, `policy.py` | PSI/KS drift, labeled performance, alert policy calibration |
| `monitoring/report.py`, `service.py` | Monitoring reports and evaluation service |

## Configuration (`configs/`)

All configuration is YAML validated by pydantic. Paths, seeds, thresholds,
and memory limits are never hard-coded.

- Data and partitions: `data.yaml`, `partition.yaml`, `raw_schema.yaml`,
  `application_validation.yaml`, `application_domains.yaml`, `cleaning.yaml`,
  `historical.yaml`.
- Features: `features.yaml` (groups and served set),
  `features_application.yaml`.
- Models and experiments: `model_lr.yaml`, `model_lgbm.yaml`,
  `calibration.yaml`, `cost.yaml`,
  `experiments/{e1,e1_served,e2,e3,e4,diagnostics,policy,rollback}.yaml`.
- Serving and operations: `api.yaml`, `serving_validation.yaml`,
  `registry.yaml`, `final_eval.yaml`, `verification.yaml`,
  `monitoring.yaml`, `load_test.yaml`.

Experiment decision rules are declared in config before running. Changing a
rule after results exist requires a new entry in `docs/DECISIONS.md`.

Config and SQL file hashes are bound into selection locks and provenance.
Cosmetic edits to those files (including comments) change the hashes and
invalidate existing locks, so they are treated as protocol changes.

## SQL (`sql/`)

- `sql/features/{application,bureau,bureau_balance,previous,installments,pos_cash,credit_card}.sql`:
  one file per source group. Aggregates are applicant-local only, and
  floating SUM/AVG inputs are explicitly ordered for deterministic results.
- `sql/validation/{history,raw_anomalies}.sql`: temporal and key validity
  checks.
- The same SQL files run in batch (DuckDB over Parquet) and online
  (in-memory DuckDB per request). Changing SQL requires the parity test.

## Tests (`tests/`)

| Layer | Path | Data |
|---|---|---|
| Unit | `tests/unit/` | Synthetic only |
| Integration | `tests/integration/` | Synthetic end-to-end (API, lock/final-eval, monitoring, parity) |
| Real data | `tests/data/`, `@pytest.mark.data` | Real `data/raw`; skipped in CI |

Tests and CI use the synthetic generator, never real data. Leakage-relevant
changes require a test that would fail if leakage were introduced. The full
gate is `make test` (ruff, format check, mypy, and pytest without the `data`
marker).

## Implementation notes

- DuckDB typed CSV reads silently round `2.5` to `3`; ingest casts from
  all-VARCHAR with a regex guard (`docs/DECISIONS.md` D8).
- Unordered floating aggregates changed 9 Selection predictions across
  processes (D16): SUM/AVG inputs are explicitly ordered.
- The Kaggle `application_test` file is unlabeled drift input, not the Test
  partition. A test guard keeps the two distinct.
- `CODE_GENDER` is excluded as a feature but required by cleaning as a
  `category_to_null` column; the API does not require gender to clean.
- OneHotEncoder treats `None` and `NaN` differently: Arrow-to-pandas
  conversion lives in one shared `frame_from_table` used by both the batch
  and online paths.
- Parquet bytes are not reproducible; raw SHA256 and row counts are the exact
  references. MLflow clients use `MLFLOW_TRACKING_URI` (HTTP server), never a
  direct `sqlite:///` URI.
- Secrets stay outside git, and the API never logs request payloads.
