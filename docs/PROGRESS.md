# RiskLab Progress

Short handoff log. The plan lives in `docs/MASTER_PLAN.md`.

- Last updated: 2026-11-01
- Current phase: Phase 2–4 code implemented (synthetic-tested); real-data E2/E3/E4 runs and the selection lock remain to be executed
- Test partition openings: 0 (see `reports/final_eval/ledger.jsonl` once it exists)

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 0. Foundations | Done | Fresh-copy check passed without real data; CI not yet run on GitHub |
| 1. MVP | Done | E1 chosen model: LightGBM (modest gain); Test not opened |
| 2. Portfolio-ready ML | Code done | Feature SQL, calibration, threshold, E2/E3/E4 runners, lock + final-eval implemented and synthetic-tested; real-data runs pending |
| 3. Portfolio-ready serving | Code done | FastAPI, registry gate/alias CLI, Docker/compose implemented and synthetic-tested; compose run pending a registered champion |
| 4. Advanced | Code done | Drift/performance/subgroup monitoring + batch simulator implemented; control-batch calibration pending real Monitoring-pool runs |
| 5. Optional | Not planned | Only on concrete need |

## Next actions (Phase 0)

- [x] Project skeleton: `pyproject.toml`, `uv.lock`, ruff, mypy, pytest markers, pre-commit, Makefile, `.gitignore`, `.env.example`
- [ ] Pin library versions and recheck against current docs (sklearn `FrozenEstimator`, MLflow allowed-hosts, LightGBM 4.x). Deferred: these libraries are added in Phase 1–3.
- [x] Typer CLI skeleton and pydantic config loading
- [x] `risklab ingest`: CSV → Parquet with explicit types, data manifest (real data: 17s, peak RSS ~2.0 GiB, row counts match)
- [x] Shared run-manifest utility
- [x] Synthetic data generator for all eight raw tables (`src/risklab/synthetic.py`)
- [x] CI workflow on synthetic data (not yet run on GitHub; action pins are major tags, SHA pinning pending)
- [x] `docs/data_contract.md` draft (numbers from `scripts/raw_anomalies.py`)

## Phase 1 tasks

- [x] Partition assignment (`risklab partition`): seed 20261101, shares 55/10/10/15/10, stratified on `TARGET`. Real data: train 169,131 / calibration 30,752 / selection 30,751 / test 46,127 / monitoring 30,750 rows, positive rates 0.08071–0.08074. `partition_hash` b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101
- [x] Role-gated loaders (`risklab.data.loaders.load_partition(role, purpose)`): only train/fit, calibration/calibrate, selection/select, monitoring/monitor are readable; Test always raises `TestAccessError`. Every load re-verifies role ids, `partition_hash` and the source Parquet/CSV hashes. AST guard test in `tests/unit/test_loaders_isolation.py`
- [x] Selection lock and `final_eval` (the only code allowed to open Test; MASTER_PLAN 4.3) — implemented in `models/select.py` + `models/final_eval.py` with ledger + reuse acknowledgment; synthetic integration test covers lock, single opening and idempotent re-run. No lock exists yet on real data, so Test is still unopened
- [x] Application validation (`risklab validate`, SQL-driven, no Pandera) and cleaning (`clean_application`, declarative `configs/cleaning.yaml`, row-wise). Real data: 0 errors; one accepted known exception (application_test 224393, `REGION_RATING_CLIENT_W_CITY = -1`). Orphan/duplicate handling for historical tables is deferred to Phase 2
- [x] Application training frame (`load_application_frame`: `load_partition` → `clean_application` → drop excluded columns) and unfitted LR / LightGBM pipelines (`risklab.models.pipelines`). Preprocessing statistics are learned only inside `.fit`; leakage tests compare fitted state with independently computed Train statistics. Added deps: pandas 3.1, scikit-learn 1.9.1, lightgbm 4.7. No metrics computed yet.
- [x] MLflow tracking server (`make mlflow`: SQLite backend, proxied artifacts, 127.0.0.1:5000) and run logging with provenance tags (partition hash, config hash, git commit and dirty flag, library versions) in `risklab.tracking`
- [x] Evaluation module (`risklab.models.evaluate`): ROC-AUC, PR-AUC, Brier, log loss, paired bootstrap, prediction files; independent check `scripts/recompute_metrics.py`
- [x] E1 (`risklab e1`): LightGBM preferred under the pre-declared rule (ROC-AUC +0.00958, 95% interval [+0.00562, +0.01312] on Selection). MLflow runs c9009e8a..., 9316731e..., b3f2b675.... Details in `docs/experiments.md`. Rerun reproduced the results exactly
- [x] Leakage diagnostics (`risklab diagnostics`): permutation AUCs within 0.5 +/- 0.006, positive control detected, no feature beyond the screening threshold (`docs/leakage.md`, run 86bbc743...)
- [x] EDA report on Train only (`scripts/eda_application.py`, written to `reports/eda/`, not committed). Notebook replaced by a script so the report is reproducible and Train-only by construction


## Phase 2–4 implementation (code complete, real runs pending)

- [x] Historical validation (`risklab validate-history`), feature SQL for all six history groups plus application ratios (`sql/features/`), registry `configs/features.yaml`, batch build (`risklab features`) and online parity path sharing the same SQL and cleaning code
- [x] Calibration (`models/calibrate.py`: FrozenEstimator calibrators, ECE, simpler-method rule), threshold/cost (`models/threshold.py`: Bayes check, sensitivity), E2/E3/E4 runners (`risklab e2|e3|e4`, configs declared in advance)
- [x] Registry promotion gate, alias CLI with audit log, FastAPI (`/predict`, `/models/current`, `/health`, `/monitoring/evaluate`, admin reload), Dockerfile + docker-compose
- [x] Monitoring: PSI/KS drift, labeled-batch performance, subgroup report with suppression, seeded batch simulator
- [ ] Run E2/E3/E4 on real data with MLflow tracking, record run IDs in `docs/experiments.md`, then lock and run `final-eval` once (requires clean tree)
- [ ] Calibrate monitoring alert thresholds on real control batches; run the rollback demo and Locust load test

## Decisions log

| Date | ID | Decision |
|---|---|---|
| 2026-11-01 | D1 | MLflow UI + Markdown reports in Phases 1–4; Streamlit optional in Phase 5 |
| 2026-11-01 | D2 | Experiment 2 decides which historical tables the API serves; correctness and simplicity first |
| 2026-11-01 | D3 | No fixed hardware or schedule; memory-bounded DuckDB + Parquet pipelines |
| 2026-11-01 | D4 | English for docs, code, comments, commit messages |
| 2026-11-01 | D5 | Test used only for final evaluation of a locked selection |
| 2026-11-01 | D6 | Serve the evaluated artifact; no refit after selection |
| 2026-11-01 | D7 | Published AUC benchmarks are context only |
| 2026-11-01 | D8 | Ingest casts from all-VARCHAR with a regex guard for integer columns, because DuckDB's typed CSV read rounds `2.5` to `3` silently |
| 2026-11-01 | D9 | Partition seed `20261101` and shares 55/10/10/15/10 are fixed. Assignment orders ids by BLAKE2b(`seed:id`) per class, with no RNG stream. Changing either requires `--overwrite` and invalidates all downstream results |
| 2026-11-01 | D10 | Validation is SQL-driven over Parquet in DuckDB, not Pandera/pandas: no new dependency, bounded memory, reusable for historical tables. Rules live in config. Known data exceptions are listed by table, check and id with a reason; stale exceptions warn |
| 2026-11-01 | D11 | Cleaning is declarative and strictly row-wise (sentinel to NULL plus flag, `XNA` to NULL), so it is safe to apply before partitioning and reusable by the API |
| 2026-11-01 | D12 | E1 and the leakage diagnostics are declared in `configs/experiments/e1.yaml` and `diagnostics.yaml` before any run: primary metric ROC-AUC, `min_gain` 0.005, 1,000 paired bootstrap resamples, 95% interval, LightGBM preferred only if gain >= min_gain and the interval lower bound > 0, otherwise Logistic Regression. Permutation band 0.02, screening threshold 0.20 |

## Open items

- Confirm default policy to exclude `CODE_GENDER` from model features (decide in Phase 2 before the selection lock).
- Set `min_gain`, ECE binning, gate tolerances, and cost assumptions in config before the related experiments.

## Handoff notes

- Phase 0 verified in a clean copy without `data/`: `uv sync --locked`, ruff, mypy, 63 tests pass; `data` tests skip.
- Ingest atomicity: all tables are staged and verified before any file is replaced. Remaining narrow window: a crash during the final file swaps or the manifest write can leave the manifest stale; the next partial ingest detects this by hash, and a full ingest repairs it.
- Parquet files are not byte-reproducible (`preserve_insertion_order=false`); raw SHA256 and row counts are the exact references.
- Raw data issues to resolve in Phase 1 validation (see `docs/data_contract.md`): orphan `SK_ID_BUREAU` / `SK_ID_PREV` rows, 653,483 duplicate installment keys, `365243` sentinels.
- MASTER_PLAN 7.1 revised: `MONTHS_BALANCE = 0` (snapshot at application) is allowed.
- Pre-API items from the pipelines review (do in Phase 3, before the API contract): (1) `CODE_GENDER` is a `category_to_null` cleaning rule column, so `clean_application` requires it even though it is excluded as a feature; the API should not have to collect gender just to clean. (2) The Arrow-to-pandas conversion (float64 casts, None→NaN in object columns, column order) lives inside `load_application_frame`; factor it into a shared `frame_from_table` used by batch and online paths, with a parity test, because `OneHotEncoder` treats `None` and `NaN` as different missing values.
- Data-contract statistics such as TARGET prevalence of sentinel rows (5.40% vs 8.66%) were computed over all of `application_train`, including Test rows. They are context only. EDA and any modelling decision must use `load_partition("train", "fit")`.
