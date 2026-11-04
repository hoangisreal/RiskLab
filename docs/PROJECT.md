# RiskLab — Project Context for Coding Agents

Read this file first. It is the canonical context map. Rules that must never be
broken live in `AGENTS.md`; frozen choices live in `docs/DECISIONS.md`; accepted
results live in `docs/RESULTS.md`; run/deploy procedures live in
`docs/OPERATIONS.md`. Historical detail is in `docs/archive/`.

## 1. What this is

- Credit-risk demo on Home Credit Default Risk: estimate P(payment difficulty)
  (`TARGET`), compare LR vs LightGBM, select historical feature sources,
  calibrate, pick a threshold, serve the frozen artifact via FastAPI, and
  demonstrate monitoring + audited rollback.
- Production-oriented demo, **not** a production credit-decisioning system.
  `approve`/`review` is illustrative. Limitations: `docs/RESULTS.md` §8.
- Language: English for all code, comments, docs, commits (`AGENTS.md` D4).

## 2. Frozen state (do not re-derive)

| Item | Value |
|---|---|
| Feature contract | 1.1 (contract-1.0 evidence is historical only) |
| Served sources | application (+5 row-wise ratios), bureau, previous, installments |
| Model inputs | 147 columns |
| Model family | LightGBM (repeated E1 on served set), calibration: none |
| Artifact SHA256 | `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676` |
| Base model run | `cbcfa9885a7e4caa982885b8adbb5cc4` |
| E3 run / E4 run | `9a84a8cc279846e88b110f06ee3ea69c` / `92aa4caf17fd434f95ad13dd80de5198` |
| Threshold | `0.08040201` (per-loan proxy: FP = 0.05×credit, FN = 0.50×credit) |
| Final lock | `402b9e050159762d` (clean source `98f5ab3`, served as registry v6) |
| Final Test run | `a9fc07a3cbaf43ab88d450ed6a3eca58` — owner-authorized reuse, opening 2 total |
| Partition | seed `20261101`, shares Train/Cal/Sel/Test/Mon = 55/10/10/15/10 |
| Partition hash | `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101` |

Any metric you cite must carry its MLflow run ID (plus lock ID for Test).
Full numbers: `docs/RESULTS.md`, `docs/model_card.md`.

## 3. Architecture and data flow

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
  -> final-eval            models/final_eval.py, ONLY code allowed to open Test
  -> MLflow registry       alias @champion, immutable serving bundle
  -> FastAPI               /predict /models/current /health /monitoring/evaluate
  -> Monitoring pool       seeded drift/performance simulations, never trains
```

Key invariants (enforced in code, tested — see `AGENTS.md` for the full list):

1. Partition roles: Train fits, Calibration fits calibrators only, Selection
   decides + promotion gate, Test only via locked `final-eval`, Monitoring
   only for simulations.
2. Labeled rows only through `load_partition`; it never returns Test.
   Never read Test outside `src/risklab/models/final_eval.py`.
3. Any cross-applicant statistic is fitted inside the sklearn Pipeline on
   Train only. Feature SQL aggregates only the applicant's own history and
   never references `TARGET`. Historical events must precede the application.
4. No class weighting/resampling unless an experiment config explicitly asks.
5. Served artifact == evaluated artifact. No refit after selection.
6. Batch and online (API) features share the same cleaning code and SQL
   files; any change to either requires the parity test to pass.

## 4. Module map (`src/risklab/`)

| Package / file | Owns | Touch it when |
|---|---|---|
| `cli.py` | Typer CLI, all `risklab *` commands | Adding/changing a command |
| `config.py` | Pydantic YAML config loading/validation | New config keys |
| `manifest.py`, `tracking.py` | Run manifests; MLflow run setup + provenance tags | Provenance/logging changes |
| `verify.py` | `risklab verify` vs `reports/reference/` aggregates | Verification rules |
| `synthetic.py` | Synthetic raw-data generator (tests/CI never use real data) | Test fixtures |
| `data/ingest.py` | CSV → Parquet, explicit types, manifest | Raw schema changes |
| `data/partition.py` | Deterministic stratified assignment (BLAKE2b seed:id) | Never change seed/shares casually — invalidates everything |
| `data/loaders.py` | Role-gated `load_partition`, hash re-verification | Partition access rules |
| `data/clean.py` | Row-wise `clean_application` from `configs/cleaning.yaml` | Cleaning rules (shared batch+API) |
| `data/validate.py`, `validate_history.py` | SQL validation vs data contract | New validation checks |
| `data/duckdb_utils.py` | Bounded-memory DuckDB helpers | Query plumbing |
| `features/registry.py` | Feature-group registry from `configs/features.yaml` | Feature groups |
| `features/application.py`, `historical.py`, `history_clean.py` | App ratios; history feature frames; event cleaning | Feature semantics (parity test!) |
| `features/build.py` | Batch build to `features/*.parquet` | Batch pipeline |
| `features/online.py`, `payload.py`, `parity.py` | Per-request features; API payload → frame; batch/API parity | API feature path (parity test!) |
| `experiments/e1.py` | E1 LR vs LightGBM (+ `--served` repeat) | Model-family comparison |
| `experiments/e234.py` | E2 sources, E3 calibration, E4 threshold runners | Selection experiments |
| `experiments/diagnostics.py`, `models/diagnostics.py` | Leakage diagnostics (permutation, screening) | Diagnostics |
| `models/pipelines.py` | Unfitted LR/LightGBM sklearn Pipelines | Preprocessing/model defs |
| `models/calibrate.py`, `threshold.py` | FrozenEstimator calibration; cost/threshold | Calibration/cost protocol |
| `models/evaluate.py` | Metrics, paired bootstrap, prediction files | Metrics |
| `models/select.py` | Selection lock (content-bound, clean-tree) | Lock format |
| `models/final_eval.py` | **Only** Test reader; ledger + reuse acknowledgment | Final evaluation only |
| `models/candidate.py`, `demo_candidate.py` | Candidate verification (no refit); rollback-demo artifact | Candidate prep |
| `models/explain.py` | TreeSHAP / LR contributions grouped to raw inputs | Explanations |
| `registry/bundle.py`, `register.py`, `gates.py`, `client.py`, `audit.py` | Serving bundle, registration, promotion gate, alias ops, audit log | Registry behavior |
| `api/main.py`, `service.py`, `model_store.py` | FastAPI app, prediction service, pinned model loading | Serving |
| `api/schemas.py`, `contract.py`, `validation.py` | Request/response contract 1.1, relational validation | API contract (see `docs/api_contract.md`) |
| `api/config.py`, `context.py`, `metadata.py`, `middleware.py` | API settings, request context, `/models/current`, logging (never payloads) | API plumbing |
| `monitoring/profile.py`, `simulate.py` | Train-fitted reference profile; seeded batch simulator | Monitoring inputs |
| `monitoring/drift.py`, `performance.py`, `policy.py` | PSI/KS drift, labeled performance, alert policy calibration | Monitoring rules |
| `monitoring/report.py`, `service.py` | Monitoring reports and evaluation service | Monitoring output |

## 5. Config map (`configs/`, 28 YAML, all pydantic-validated)

- Data/partitions: `data.yaml`, `partition.yaml`, `raw_schema.yaml`,
  `application_validation.yaml`, `application_domains.yaml`, `cleaning.yaml`,
  `historical.yaml`.
- Features: `features.yaml` (groups/served set), `features_application.yaml`.
- Models/experiments: `model_lr.yaml`, `model_lgbm.yaml`, `calibration.yaml`,
  `cost.yaml`, `experiments/{e1,e1_served,e2,e3,e4,diagnostics,policy,rollback}.yaml`.
- Serving/ops: `api.yaml`, `serving_validation.yaml`, `registry.yaml`,
  `final_eval.yaml`, `verification.yaml`, `monitoring.yaml`, `load_test.yaml`.
- Never hard-code paths/seeds/thresholds in code; add a config key instead.
- Experiment rules are declared in config **before** running; changing one
  after results exist requires a note in `docs/DECISIONS.md`.
- Caution: config/SQL file hashes are bound into locks and provenance.
  Do not edit configs or SQL cosmetically (comments included) without
  checking what hashes them.

## 6. SQL (`sql/`)

- `sql/features/{application,bureau,bureau_balance,previous,installments,pos_cash,credit_card}.sql`
  — one file per source group; applicant-local aggregates only; explicit
  ordering of floating SUM/AVG inputs (contract-1.1 determinism fix, D16).
- `sql/validation/{history,raw_anomalies}.sql` — temporal/key validity checks.
- Same SQL files run in batch (DuckDB over Parquet) and online (in-memory
  DuckDB per request). Changing SQL requires the parity test.

## 7. Tests (`tests/`, ~380 non-data tests)

| Layer | Path | Data |
|---|---|---|
| Unit | `tests/unit/` | Synthetic only |
| Integration | `tests/integration/` | Synthetic end-to-end (API, lock/final-eval, monitoring, parity) |
| Real data | `tests/data/`, `@pytest.mark.data` | Real `data/raw`; skipped in CI |
| Fixtures | `tests/fixtures/synthetic.py` re-exports `risklab.synthetic` | — |

Markers: `unit`, `integration`, `data`, `slow`. Leakage-relevant changes need
a test that would fail if leakage were introduced. Gate: `make test`
(ruff + format check + mypy + pytest non-`data`).

## 8. Where to make a change (routing)

| Task | Start at | Must also check |
|---|---|---|
| New/changed feature | `sql/features/*.sql`, `features/`, `configs/features*.yaml` | Parity test, `docs/data_contract.md`, provenance hashes |
| Cleaning rule | `data/clean.py`, `configs/cleaning.yaml` | Batch+API share it; parity test |
| Model/hyperparams | `models/pipelines.py`, `configs/model_*.yaml`, experiment config | Declare rule before running; log run ID |
| Metric | `models/evaluate.py` | `scripts/recompute_metrics.py` independent check |
| API request/response | `api/schemas.py`, `api/contract.py`, `configs/api.yaml` | `docs/api_contract.md`, golden/parity tests, no payload logging |
| Registry/promotion | `registry/` | Audit log, gate in `registry/gates.py`, never move aliases outside CLI |
| Monitoring/alerts | `monitoring/`, `configs/monitoring.yaml` | Monitoring pool only; never train/select on it |
| Anything touching Test | **Stop.** Only `models/final_eval.py` + explicit owner approval + recorded reuse acknowledgment | `docs/DECISIONS.md` D5/D18 |

## 9. Commands

Canonical table is in `AGENTS.md` §Commands. Everyday: `make setup`,
`make test`, `uv run pytest -m data`, `make ingest|partition|validate`,
`risklab e1|e2|e3|e4|diagnostics`, `risklab verify`,
`risklab prepare-candidate|lock-selection|registry ...|monitoring ...`,
`make reproduce OUT=DIR`, `docker compose up`.

## 10. Gotchas learned the hard way

- DuckDB typed CSV read silently rounds `2.5` → `3`; ingest casts from
  all-VARCHAR with a regex guard (D8).
- Unordered floating aggregates changed 9 Selection predictions across
  processes (D16): SUM/AVG inputs are explicitly ordered; never remove that.
- Kaggle `application_test` is **unlabeled** drift input, not the Test
  partition. The AST guard in `tests/unit/test_loaders_isolation.py`
  distinguishes them; keep the exact filename distinction.
- `CODE_GENDER` is excluded as a feature but required by cleaning as a
  `category_to_null` column; the API must not demand gender to clean.
- OneHotEncoder treats `None` and `NaN` differently: Arrow→pandas conversion
  lives in one shared `frame_from_table` used by batch and online paths.
- Parquet bytes are not reproducible; raw SHA256 + row counts are the exact
  references. MLflow clients use `MLFLOW_TRACKING_URI` (HTTP server), never a
  direct `sqlite:///` URI.
- `.env`/Kaggle credentials stay outside git; the API never logs payloads.
