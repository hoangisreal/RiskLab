# RiskLab Master Implementation Plan

Credit Risk Prediction & Model Monitoring on the Home Credit Default Risk dataset.

- Status: approved direction, not started. Live status is tracked in `docs/PROGRESS.md`.
- Last revised: 2026-11-01.
- Audience: the project owner and any coding agent working in this repository (see `AGENTS.md`).

---

## 1. Purpose and scope

RiskLab estimates the probability that a loan applicant will have payment difficulties, compares models, explains individual predictions, manages model versions, and evaluates model behavior over (simulated) time.

RiskLab is a **production-oriented demo**. It applies production engineering practices (data contracts, versioned artifacts, test isolation, reproducible pipelines, containerized serving, monitoring, rollback) to a static public dataset. It is **not** a production credit-decisioning system. Section 16 lists the gaps explicitly.

### Definition of Done (project level)

A person can clone the repository, prepare the data by following the docs, run training, reproduce the reported results within documented tolerances, start the prediction API, and view the model comparison. The owner can explain why the model was chosen, why the threshold was chosen, and under which conditions the model is expected to fail.

### Locked decisions

| # | Decision | Date |
|---|---|---|
| D1 | Reporting uses the MLflow UI and generated Markdown reports in Phases 1–4. Streamlit is optional in Phase 5. | 2026-11-01 |
| D2 | Experiment 2 decides which historical tables the API serves, with correctness and simplicity taking priority over small metric gains. | 2026-11-01 |
| D3 | No fixed hardware or schedule. Pipelines must be memory-bounded (DuckDB + Parquet) and must not require loading raw tables into pandas. | 2026-11-01 |
| D4 | Documentation, code, comments, and commit messages are in English. | 2026-11-01 |
| D5 | The Test partition is used only for final evaluation of a locked selection (Section 4). | 2026-11-01 |
| D6 | The served artifact is the exact artifact that was evaluated. No refit on additional partitions after selection. | 2026-11-01 |
| D7 | Published AUC values for this dataset are context only. They are neither acceptance criteria nor evidence of leakage. | 2026-11-01 |

---

## 2. Problem analysis

| Dataset property | Design consequence |
|---|---|
| `application_train`: ~307.5k rows, 122 columns, ~8.1% positive `TARGET` | Imbalanced. Primary metrics: ROC-AUC, PR-AUC (baseline ≈ prevalence), Brier score, log loss. Accuracy is not reported as a headline metric. |
| `application_test` (Kaggle) has no labels | Cannot be used for evaluation. All labeled partitions come from `application_train`. The Kaggle test file is used as a **real, unlabeled drift sample**. |
| All time fields (`DAYS_*`, `MONTHS_BALANCE`) are relative to each application date. There is no absolute application date. | A reliable temporal (out-of-time) split is impossible. Use stratified holdout partitions and document this limitation. Temporal drift is simulated. |
| Six historical tables. Largest: `bureau_balance` (~27M rows), `installments_payments` (~13.6M rows). ~2.5 GB of CSV in total. | Aggregate in DuckDB over Parquet with a memory limit and spill directory. Only per-applicant aggregates enter Python. |
| Sentinels (`365243` in `DAYS_EMPLOYED`, `DAYS_FIRST_DUE`, …), `XNA` categories, a few `CODE_GENDER = XNA` rows | Explicit, tested cleaning rules. |
| `TARGET` = "payment difficulties", not default or loss. Only approved loans are present. | Selection bias (reject inference problem). Cost estimates are proxies. Both go into the model card. |
| Dataset license: Kaggle competition rules | Raw data is never committed or redistributed. Tests and CI use synthetic data. |

---

## 3. Architecture

```
Kaggle CSV ──► Parquet + manifest (SHA256, row counts)
                 │
                 ▼
        Validation (Pandera + SQL checks) ──► validation report
                 │
                 ▼
   sql/features/*.sql (DuckDB) ──► features/<group>.parquet ──► feature table
                 │                                   ▲
                 │                     same SQL, in-memory DuckDB, per request
                 ▼                                   │
   Partition assignment (IDs + hash) ──► role-gated loaders
                 │
                 ▼
   sklearn Pipeline (preprocess + LR | LightGBM) ── Train
                 │
   Calibrator (FrozenEstimator) ── Calibration
                 │
   Model / calibration / threshold selection ── Selection ──► selection lock
                 │
   Final evaluation (once per lock) ── Test ──► final report + ledger
                 │
   MLflow tracking server (SQLite + proxied artifacts) ── registry alias @champion
                 │
   FastAPI: /predict  /models/current  /health  /monitoring/evaluate
                 │
   Monitoring batches ── Monitoring pool + Kaggle application_test
```

### Technology choices

| Concern | Choice | Rejected alternative and reason |
|---|---|---|
| Environment | Python 3.12, `uv` with `uv.lock`, Typer CLI, Makefile | Poetry/conda: slower, no extra benefit here |
| Data processing | DuckDB SQL over Parquet. pandas/pyarrow only for the final feature matrix. | pandas on raw tables: memory blow-up on `bureau_balance` |
| Validation | Pandera schemas + SQL assertion queries | Great Expectations: heavier than needed |
| Models | scikit-learn `LogisticRegression`, LightGBM `LGBMClassifier` inside sklearn `Pipeline` | XGBoost/CatBoost: no question in scope needs them |
| Calibration | `CalibratedClassifierCV(FrozenEstimator(model), method=...)` | `cv="prefit"`: deprecated since sklearn 1.6 |
| Tracking and registry | MLflow 3 tracking server, SQLite backend, proxied local artifact store, alias-based registry | Registry stages: deprecated in MLflow. File store: legacy. |
| Explanations | LightGBM `pred_contrib=True` (TreeSHAP); LR coefficient × transformed value | `shap` package: extra dependency for the same values |
| Drift | Own PSI / KS / chi-square implementation with unit tests | Evidently: optional in Phase 5 |
| Monitoring storage | JSON reports on disk | PostgreSQL: added only if operational history is required |
| Serving | FastAPI + Uvicorn, Pydantic v2 | MLflow `pyfunc` serving: no control over contract, explanations, or reload |
| Load testing | Locust | k6: second language for no benefit |

Out of scope (YAGNI): Kafka, Kubernetes, Airflow, feature stores, DVC, deep learning, AutoML, SMOTE.

---

## 4. Data partitioning and test isolation

### 4.1 Partitions

`application_train` is split once, stratified on `TARGET`, with a fixed seed. Partition membership is stored as `SK_ID_CURR` lists plus a content hash.

| Partition | Share | ≈ Rows | ≈ Positives | Sole purpose |
|---|---|---|---|---|
| Train | 55% | 169k | 13.6k | Fit preprocessing and models. Inner 5-fold CV for hyperparameters and number of boosting rounds. |
| Calibration | 10% | 30.7k | 2.5k | Fit calibrators on frozen models. Nothing else. |
| Selection | 10% | 30.7k | 2.5k | Compare model families, feature groups, calibration methods (including none); choose threshold; run promotion gate. |
| Test | 15% | 46k | 3.7k | Final evaluation of a locked selection. Never used for any choice. |
| Monitoring pool | 10% | 30.7k | 2.5k | Source of simulated monitoring batches in Phase 4. Never used for training or selection. |

Plus: Kaggle `application_test` (48.7k rows, unlabeled) as a real drift sample.

Rationale:

- Each data-dependent decision has a partition that the decided-upon component never saw while being fitted. Calibrators are not fitted on data that trained the model, and calibration method and threshold are not chosen on data that fitted the calibrator.
- The Selection partition is reused for several low-dimensional decisions (model family, feature set, calibration method, threshold). This makes Selection metrics mildly optimistic. That is acceptable because the unbiased estimate comes from Test. Splitting Selection further was rejected because each sub-split would have fewer than ~1.3k positives, which makes AUC and calibration comparisons too noisy.
- A separate Monitoring pool keeps the Test partition single-use even after the monitoring and rollback demos.
- No refit after selection (D6). Refitting on Train+Calibration+Selection would invalidate the calibrator and threshold and would serve an artifact nobody evaluated. The cost is ~20% less training data.

### 4.2 Enforcement in code

- Partitions are written to separate files. A single loader `load_partition(role, purpose)` is the only way to read labeled rows.
- Loading `test` is allowed only from the final-evaluation module and requires a valid selection lock. A unit test asserts that no other module can obtain Test rows.
- Partition disjointness, coverage, and stratification are tested.
- The partition hash is logged on every MLflow run and checked at load time.

### 4.3 Selection lock and test ledger

Before Test is opened, `risklab lock-selection` writes `reports/locks/<lock_id>.json` containing: model run ID, feature set ID and contract version, calibration method, threshold, config hashes, partition hash, data manifest hash, git commit (clean tree required), library versions.

`risklab final-eval --lock <lock_id>`:

1. Verifies the lock (hashes, clean tree, artifact exists).
2. Evaluates on Test once and writes `reports/final_eval/<lock_id>/`.
3. Appends an entry to `reports/final_eval/ledger.jsonl` (timestamp, lock ID, metrics).
4. Re-running with the same lock returns the stored result without re-evaluating.
5. Running with a new lock after Test has already been opened is allowed only with `--acknowledge-test-reuse "<reason>"`. Every report then states how many times Test has been opened.

Test results never feed back into selection. If a Test result prompts a change, the change goes through Selection again, and the next Test opening is disclosed as a reuse.

---

## 5. Calibration and threshold protocol

1. **Base model fit (Train).** The Pipeline (preprocessing + estimator) is fitted on Train. LightGBM's number of rounds comes from inner 5-fold CV on Train (`lgb.cv` with early stopping), followed by a refit on all of Train with that count. No class weighting or resampling in candidate models (it distorts probabilities). One diagnostic run with `scale_pos_weight` is included in Experiment 3 to show the effect.
2. **Calibrator fit (Calibration).** For each method in `{none, sigmoid, isotonic}`: `CalibratedClassifierCV(FrozenEstimator(fitted_pipeline), method=m).fit(X_cal, y_cal)`. The pipeline is frozen and not refitted.
3. **Method selection (Selection).** Compare log loss (primary), Brier score, ECE (15 equal-frequency bins, fixed in config before running), and reliability diagrams on Selection. Report paired bootstrap 95% CIs for differences. Decision rule: choose the method with the lowest Selection log loss. If its CI against a simpler method (`none` < `sigmoid` < `isotonic`) includes zero, choose the simpler method.
4. **Threshold selection (Selection).** Using calibrated probabilities on Selection, compute expected cost over a threshold grid with cost assumptions from `configs/cost.yaml`. Choose the cost-minimizing threshold. Sanity check: for calibrated probabilities and constant costs, the empirical optimum should be close to the Bayes threshold `C_FP / (C_FP + C_FN)` (reject when `p ≥ t`). A large gap points to miscalibration or a bug.
5. **Lock, then Test.** Test reports discrimination, calibration, and cost at the locked threshold. Test is never used to tune any of these.

Sanity check across steps: calibration is monotonic, so ROC-AUC should be nearly unchanged by sigmoid. Isotonic introduces ties and can shift AUC slightly. A large AUC change indicates a bug.

---

## 6. Experiment protocol

Every experiment has a config file, a pre-declared decision rule, and a fixed seed. It logs to its own MLflow experiment and produces a section in `docs/experiments.md` with tables, plots, the decision, and MLflow run IDs.

Common rules:

- Hyperparameters are tuned only by inner CV on Train, with a fixed and logged search budget.
- Comparisons are made on Selection with **paired bootstrap** (≥ 1,000 resamples, fixed seed) CIs for metric differences. Inner-CV fold scores on Train are reported as a stability indicator, not as the decision basis.
- Decision rules and the minimum practical effect `min_gain` are written into the experiment config **before** the experiment runs. Changing them afterwards requires a note in `docs/PROGRESS.md`.

| ID | Question | Design | Decision rule |
|---|---|---|---|
| E1 | Logistic Regression vs LightGBM | Same feature set, same partitions, each tuned by inner CV on Train. Run on application features (Phase 1), then repeated on the Phase 2 feature set. | Prefer LightGBM only if ΔROC-AUC ≥ `min_gain` and the 95% CI lower bound > 0. Otherwise prefer LR (simpler, interpretable). Also report PR-AUC, Brier, and log loss. |
| E2 | Application-only vs added historical features | Ablation: application → +bureau(+balance) → +previous → +installments → +POS → +credit card; plus leave-one-group-out from the full set. Fixed LightGBM params. | Serve a table group only if its marginal ΔROC-AUC ≥ `min_gain` with CI lower bound > 0. Also weigh serving cost (payload size, contract complexity). This decision fixes the API contract (D2). |
| E3 | Raw vs calibrated probabilities | Section 5, steps 2–3, plus the `scale_pos_weight` diagnostic run. | Section 5, step 3. |
| E4 | Threshold vs precision, recall, approval rate, expected cost | Section 5, step 4. Sensitivity over `C_FN / C_FP` ∈ [2, 20]. Phase 4 adds per-loan costs. | Cost-minimizing threshold on Selection under the base cost assumption. Sensitivity is reported, not optimized. |

Pre-lock policy analysis (Phase 2): compare with and without sensitive attributes (`CODE_GENDER`, age-derived features) on Selection. Default policy is to exclude `CODE_GENDER` from model features. Age is kept unless the analysis or owner decision says otherwise. The decision is recorded before locking.

### Reference benchmarks (context only, D7)

Public Kaggle results put application-only models around 0.74–0.76 ROC-AUC and multi-table LightGBM around 0.78–0.79 (private leaderboard tops at ~0.80 with heavy ensembling). Values far outside these ranges prompt a review of the pipeline. They do not prove or disprove leakage, and they are not acceptance criteria. Leakage is assessed with the controls in Section 7.

---

## 7. Leakage controls

Leakage is prevented by construction and checked by tests and diagnostics. Benchmarks are not used for this purpose.

### 7.1 Rules

| Rule | How it is enforced |
|---|---|
| Per-applicant aggregates over that applicant's own history use no labels and no cross-applicant statistics. They may be computed before partitioning. | SQL files only `GROUP BY SK_ID_CURR` (or by the intermediate key). Code review checklist. Test that no SQL file references `TARGET`. |
| Any statistic computed across applicants (imputation values, scaling, encodings, frequency or target encoding, feature selection, binning) is fitted inside the sklearn `Pipeline` on Train only. | Unit test: fitted transformer statistics equal Train-only statistics and do not change when other partitions change. |
| Historical events must precede the application. | SQL validation: realized-event fields (`DAYS_ENDDATE_FACT`, `DAYS_ENTRY_PAYMENT`, `DAYS_INSTALMENT` for paid installments, `MONTHS_BALANCE`) must be ≤ 0. `MONTHS_BALANCE = 0` is the snapshot at application (present in bureau_balance per the dataset column description) and is allowed; POS and credit card data end at -1. Violations are counted and reported, and offending rows are excluded with a logged count. Planned fields known at application time (for example `DAYS_CREDIT_ENDDATE > 0`) are allowed and documented. |
| Sentinels are not treated as real values. | `365243` → NULL plus an indicator flag where informative. Tested. |
| No decision uses Test. | Section 4.2. |

### 7.2 Diagnostics (run in Phase 1 and Phase 2)

- **Label permutation test:** train the full pipeline on Train with shuffled labels and evaluate on Selection. ROC-AUC must be within the expected noise band around 0.5 (for example, |AUC − 0.5| < 0.02). A higher value means information is leaking through the pipeline.
- **Single-feature screening:** univariate ROC-AUC per feature on Train. Any feature with |AUC − 0.5| > 0.2 other than known strong signals (`EXT_SOURCE_*`) is investigated and documented.
- **Importance spikes:** a single feature with a dominant share of gain is investigated.
- **Train–Selection gap:** a large gap between Train-CV and Selection metrics is investigated (overfitting or partition mismatch).

All findings go to `docs/leakage.md`.

---

## 8. Feature engineering and memory design

- **Ingest:** DuckDB reads each CSV with an explicit column-type mapping (not sampled inference) and writes Parquet (zstd). `data/manifest.json` records SHA256 of the raw files, Parquet row counts, and schema.
- **DuckDB settings** from config: `memory_limit` (default 4GB), `temp_directory` (spill), `threads`, `preserve_insertion_order=false`.
- **Feature SQL:** one file per source in `sql/features/`. Two-level aggregations are done in SQL:
  - `bureau_balance → bureau → applicant`
  - `installments / POS / credit card → previous application → applicant`
- **Feature groups** (candidates, finalized by E2):
  - application: cleaned raw fields; row-wise ratios such as credit/income, annuity/income, credit/goods, employment/age.
  - bureau: counts; active ratio; debt and limit sums and ratios; overdue amounts; DPD months from balance.
  - previous: counts by status; refusal rate; requested vs approved amount ratio.
  - installments: late and underpaid ratios; mean and max days late; last-12-month windows.
  - POS / credit card: DPD; utilization.
- **No-history semantics:** each group outputs `<group>_record_count` as `COALESCE(count, 0)` via a left join from the applicant table. Other aggregates are NULL when there are no records. The API must reproduce exactly this (Section 10.2).
- **Output:** each group is written to `features/<group>.parquet` keyed by `SK_ID_CURR`, then joined into the training matrix. Columns are cast to float32 where lossless enough, and only the selected columns are loaded into memory (~0.5 GB for ~300k × ~400 float32).
- **Feature registry:** `configs/features.yaml` lists each feature (name, group, dtype, nullable, source SQL). It is the single source for the training column list and the API request schema.

---

## 9. MLflow architecture

### 9.1 Components

| Component | Configuration |
|---|---|
| Tracking server | `mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri sqlite:////mlflow/mlflow.db --artifacts-destination /mlflow/artifacts --allowed-hosts "mlflow:5000,localhost:*,127.0.0.1:*"` |
| Backend store | SQLite file `mlflow.db` in the `mlflow-data/` volume. It holds runs, metrics, params, tags, and the model registry. |
| Artifact store | Local directory `/mlflow/artifacts` in the same volume, served by the tracking server (proxied artifact access). Artifact URIs have the form `mlflow-artifacts:/...` and do not depend on any host path. |
| Clients | Training CLI and API both use `MLFLOW_TRACKING_URI`: `http://localhost:5000` from the host and `http://mlflow:5000` inside compose. No client ever uses a `sqlite:///` URI directly. |

Why a server instead of direct `sqlite:///` from clients: without a server, artifact URIs are recorded as absolute host paths, and those paths do not exist inside containers. Routing all access through the server makes host and container runs see the same registry and artifacts.

`--allowed-hosts`: MLflow ≥ 3.5 validates the Host header and by default accepts localhost and private IPs only. The compose service name `mlflow` must be added explicitly. This is verified in Phase 3 with an integration test.

Limitations, documented: SQLite supports one writer at a time (fine for one user, not for parallel training jobs). The tracking server has no authentication, so its port is bound to `127.0.0.1` only. Backup means stopping the server and copying `mlflow-data/`, or using `sqlite3 .backup` for the database plus copying the artifact directory.

### 9.2 Runs and experiments

- MLflow experiments: `risklab-e1-model-family`, `risklab-e2-feature-groups`, `risklab-e3-calibration`, `risklab-e4-threshold`, `risklab-candidates`, `risklab-final-eval`.
- Every run logs: resolved config (artifact) and config hash; partition hash; data manifest hash; feature set ID and contract version; git commit and dirty flag; library versions; seeds; metrics per partition; plots (ROC, PR, reliability, cost curve); prediction files for Selection (and Test for final-eval) as Parquet (`SK_ID_CURR`, score); feature importance.
- Prediction files enable independent metric recomputation (Section 11.3).

### 9.3 Model registry

- Registered model: `risklab-credit-risk`.
- The logged model is one sklearn object: the calibrated wrapper around the frozen Pipeline. It is logged with the `mlflow.sklearn` flavor, an input signature generated from the feature registry, and pinned requirements.
- Version artifacts and tags: `serving_config.json` (threshold, calibration method, contract version, feature list, explanation settings), `reference_profile.json` (per-feature bin edges and proportions from Train; score distribution from Selection), model card, gate report. Tags: `contract_version`, `lock_id`, `gate_status`, `git_sha`, `partition_hash`.
- Aliases: `champion` is served. `challenger` is optional.
- Registration requires a clean git tree and a passing gate (Section 12, Phase 3). A force flag exists for the rollback demo, requires a reason, and is recorded in the audit log.
- MLflow does not keep alias history, so `reports/registry_audit.jsonl` records every alias change (timestamp, from, to, actor, reason).

### 9.4 How the API obtains the champion

1. At startup: `MlflowClient().get_model_version_by_alias("risklab-credit-risk", "champion")` resolves a **version number**. The API then loads `models:/risklab-credit-risk/<version>` (pinned version, not the alias, so metadata and model cannot diverge during an alias change).
2. Validate before serving: `contract_version` matches the API's supported contract; runtime versions of sklearn, LightGBM, and Python match the model's requirements; `serving_config.json` is present and valid. On failure the API does not become ready.
3. Readiness (`/health`) reports not-ready until a model is loaded. Liveness is independent of it.
4. Reload: `POST /admin/models/reload` (API key from env) resolves the alias again, loads and validates the new version in the background, then swaps the in-memory reference atomically. If loading fails, the current model keeps serving and the error is returned.
5. If the tracking server is unreachable after startup, the loaded model keeps serving. Only reload fails.

Production note (documented, not implemented): a production system would typically bake or pull an immutable model bundle at deploy time so serving does not depend on the registry at runtime. Here the registry is queried directly to keep rollback a one-command operation.

### 9.5 Docker consistency

- One multi-stage `Dockerfile` built from the same `uv.lock`, with targets `train` (training, CLI, MLflow server) and `api` (runtime only). Both run as a non-root user.
- `docker-compose.yml` services:
  - `mlflow` (target `train`, runs `mlflow server`, volume `./mlflow-data:/mlflow`, port `127.0.0.1:5000`);
  - `api` (target `api`, `MLFLOW_TRACKING_URI=http://mlflow:5000`, port `127.0.0.1:8000`, depends on a healthy `mlflow`);
  - `train` (profile `jobs`, data mounted read-only at `/data`, reports mounted at `/app/reports`).
- Candidate models intended for registration are trained in the `train` container (`docker compose run --rm train risklab ...`), so the Python and library versions of the trained artifact match the API image. Host training is allowed for exploration; the version check in 9.4 step 2 catches mismatches.
- The MLflow server and client versions come from the same lock file.

---

## 10. API design and data contract

### 10.1 Endpoints

| Endpoint | Behavior |
|---|---|
| `POST /predict` | Accepts a versioned request (10.2). Runs cleaning and feature SQL in in-memory DuckDB, then the calibrated model. Returns probability, threshold, recommendation, model version, contract version, `sources_used`, warnings, and an optional explanation. |
| `GET /models/current` | Champion version, run ID, lock ID, training data/partition/feature hashes, Selection and final Test metrics, calibration method, threshold, contract version, registration time. |
| `GET /health` | Liveness and readiness (model loaded and validated). |
| `POST /monitoring/evaluate` | Phase 4. Accepts a `batch_id` registered in the monitoring batch manifest (never a file path) and an optional model version. Returns the monitoring report described in Phase 4 (Section 14). |
| `POST /admin/models/reload` | API-key protected. Reloads the champion (9.4). |
| `GET /demo/payloads/{sk_id_curr}` | Optional, disabled by default. Builds a request payload from the Monitoring pool, clearly labeled as demo data. `/predict` itself never accepts a customer ID. |

### 10.2 Request contract

```json
{
  "contract_version": "1.0",
  "request_id": "optional-client-trace-id",
  "application": { "AMT_CREDIT": 406597.5, "AMT_INCOME_TOTAL": 202500.0, "...": "..." },
  "history": {
    "bureau":                { "status": "provided", "records": [ { "bureau_ref": "b1", "...": "..." } ] },
    "bureau_balance":        { "status": "provided", "records": [ { "bureau_ref": "b1", "MONTHS_BALANCE": -1, "STATUS": "0" } ] },
    "previous_applications": { "status": "provided", "records": [] },
    "installments":          { "status": "not_provided" }
  }
}
```

Each history source carries an explicit status. This separates "no credit history" from "client did not send the data":

| State | Meaning | Server behavior |
|---|---|---|
| `provided` with records | Applicant's records for this source | Aggregate with the training SQL. |
| `provided` with `records: []` | Client asserts the applicant has **no** records in this source | Produce the training no-history values: `<group>_record_count = 0`, other aggregates NULL. |
| `not_provided`, or the source key is absent | Data unavailable or not retrieved | If the served model uses this source: **reject with 422** and name the missing sources. Never impute it as "no history", because missing history and empty history carry different risk. A degraded fallback model is an optional Phase 5 feature and requires its own validated version. |
| Source not used by the served model | — | Ignored and listed in `warnings`. |

Additional contract rules:

- Which sources are required is defined by the served model's `serving_config.json` (decided by E2) and published in the OpenAPI schema.
- In the training data, "no rows" can only mean "no records". The Kaggle data cannot distinguish "not retrieved". This assumption is documented in the data contract.
- Child records reference parent records within the same request (`bureau_ref`, `prev_ref`). Dangling references → 422.
- Application field types, nullability, and numeric ranges come from the feature registry and data profile. Out-of-domain values → 422. Unseen categories are accepted, mapped to the infrequent/unknown bucket, and reported in `warnings`.
- Limits: maximum records per source and a maximum request body size (configurable).
- The contract version changes whenever the served feature set changes. The API refuses models with a different contract version.
- Logs contain request ID, model version, latency, and status. Payloads are never logged.

### 10.3 Response

```json
{
  "request_id": "…",
  "model_version": "7",
  "contract_version": "1.0",
  "probability": 0.137,
  "threshold": 0.162,
  "recommendation": "review",
  "sources_used": ["application", "bureau", "bureau_balance", "previous_applications"],
  "warnings": [],
  "explanation": { "space": "log_odds_uncalibrated", "top_contributions": [ { "feature": "EXT_SOURCE_3", "value": 0.21, "contribution": 0.48 } ] }
}
```

The recommendation is advisory (`approve` / `review`), not an automated credit decision. Explanations describe the uncalibrated model's log-odds contributions. They are not causal and not regulatory adverse-action reasons. For LR, one-hot contributions are summed back to the original feature.

---

## 11. Reproducibility and independent verification

### 11.1 Determinism

- All seeds come from config. LightGBM runs with `deterministic=True`, `force_row_wise=True`, and fixed `num_threads`. sklearn components have a fixed `random_state`. Bootstrap uses a seeded generator.
- DuckDB outputs are sorted by key before writing. Parallel floating-point aggregation can differ in the last bits, so feature tables are compared with a tolerance. Hashes identify artifacts; tolerances verify them.
- Partition assignment is a pure function of (`SK_ID_CURR`, `TARGET`, seed). The committed partition hash lets anyone confirm they regenerated identical partitions.

### 11.2 Run manifests

Each pipeline step (`ingest`, `validate`, `features`, `partition`, `train`, `calibrate`, `select`, `final-eval`) writes `reports/manifests/<step>.json` with input hashes, config hash, git commit, dirty flag, library versions, platform, output hashes, and summary statistics. Manifests are also logged to MLflow.

### 11.3 Verification

- `make reproduce`: runs the whole pipeline from raw data into a fresh output root.
- `risklab verify --reference reports/reference/`: compares a fresh run against committed reference manifests:
  - raw data SHA256: exact;
  - partition hash: exact;
  - feature tables: max relative difference ≤ 1e-9;
  - metrics: ≤ 1e-6 on the same platform and image, ≤ 1e-3 across platforms (documented, not guaranteed).
- Independent metric check: `scripts/recompute_metrics.py` imports only pandas and `sklearn.metrics`, not RiskLab code. It reads logged prediction files and labels, recomputes ROC-AUC, PR-AUC, Brier, and log loss, and compares them with the logged values.
- Determinism test in CI: the full pipeline runs twice on synthetic data, and the outputs must match.
- Committed references contain only hashes and aggregate metrics, never row-level data.

---

## 12. Testing strategy

| Layer | Examples | Data |
|---|---|---|
| Unit | Cleaning rules; each feature SQL on hand-written fixtures with known expected aggregates; no-history semantics; PSI/KS on known distributions; cost function; Bayes-threshold check on synthetic calibrated scores; contract validation (status states, dangling refs, limits) | Synthetic |
| Leakage | Partition disjointness and coverage; Train-only fit statistics; Test loader gate; no `TARGET` reference in SQL; permutation test on synthetic data | Synthetic |
| Integration | End-to-end ingest → features → train → calibrate → register → API predict on the synthetic dataset; MLflow server in compose reachable by service name; reload and rollback | Synthetic |
| Parity (`@pytest.mark.data`) | For ~500 sampled applicants: features computed through the API path equal the batch feature table (tolerance 1e-9), and predictions match | Real data |
| API | 422 cases, readiness before model load, `/models/current` content, golden predictions for a pinned model version | Synthetic |
| Behavioral | Directional checks, for example higher `EXT_SOURCE_*` lowers mean predicted risk | Real data |
| Reproducibility | Run twice and compare; `risklab verify` | Both |

Pytest markers: `unit`, `integration`, `data` (requires real data, skipped in CI), `slow`. CI (GitHub Actions) runs lint, type check, and all non-`data` tests on synthetic data.

The synthetic generator (`tests/fixtures/synthetic.py`) produces all seven tables with the real schemas, valid keys, a learnable signal, deliberate edge cases (sentinels, `XNA`, applicants without history), and a fixed seed.

---

## 13. Repository structure

```
RiskLab/
  AGENTS.md
  README.md
  pyproject.toml  uv.lock  Makefile  Dockerfile  docker-compose.yml  .env.example
  configs/        data.yaml  partition.yaml  features.yaml  model_lr.yaml  model_lgbm.yaml
                  calibration.yaml  cost.yaml  experiments/e1.yaml … e4.yaml  monitoring.yaml  api.yaml
  sql/
    validation/   *.sql
    features/     application.sql  bureau.sql  previous.sql  installments.sql  pos_cash.sql  credit_card.sql
  src/risklab/
    config.py  cli.py  manifest.py
    data/         ingest.py  validate.py  clean.py  partition.py  loaders.py
    features/     build.py  registry.py  online.py
    models/       pipelines.py  train.py  calibrate.py  evaluate.py  threshold.py  explain.py  select.py  final_eval.py
    registry/     client.py  gates.py  promote.py  audit.py
    monitoring/   drift.py  performance.py  subgroups.py  simulate.py  report.py
    api/          main.py  schemas.py  service.py  model_store.py  routers/
  scripts/        recompute_metrics.py
  tests/          unit/  integration/  data/  fixtures/
  notebooks/      01_eda.ipynb  02_feature_analysis.ipynb
  docs/           MASTER_PLAN.md  PROGRESS.md  data_contract.md  leakage.md  experiments.md
                  model_card.md  api_contract.md  monitoring.md  deployment.md  reproducibility.md  limitations.md
  reports/        reference/ (committed: hashes, aggregate metrics)   everything else gitignored
  data/  mlflow-data/   (gitignored)
```

Notebooks are for exploration and presentation only. Any logic used by the pipeline lives in `src/`.

---

## 14. Phases

Phases have no calendar deadlines (D3). A phase is complete when its exit criteria are met and recorded in `docs/PROGRESS.md`.

### Phase 0: Foundations

**Learn:** `uv` workflows; src-layout packaging; Kaggle API and competition rules; DuckDB CSV reading with explicit types; Pandera basics.

**Tasks:**
1. Project skeleton: `pyproject.toml`, `uv.lock`, ruff (lint + format), mypy or pyright (basic), pytest with markers, pre-commit, Makefile, `.gitignore` (data, mlflow-data, reports except reference), `.env.example`.
2. Typer CLI skeleton (`risklab --help`) and pydantic config loading with path resolution and validation.
3. `risklab ingest`: CSV → Parquet with explicit types, `data/manifest.json` with SHA256 and row counts, memory-bounded DuckDB settings.
4. Synthetic data generator for all seven tables.
5. Manifest utility shared by all steps (Section 11.2).
6. CI workflow: lint, type check, unit tests on synthetic data.
7. `docs/data_contract.md` draft (tables, keys, known anomalies).

**Deliverables:** runnable skeleton; ingest command; synthetic generator; CI.

**Exit criteria:**
- A fresh clone runs `make setup`, `make test`, and `make ingest` (with data present) successfully.
- The ingest manifest matches the raw files, and Parquet row counts match the CSVs.
- Peak memory during ingest stays within the configured DuckDB limit plus a documented overhead.

**Risks:** CSV type inference errors (mitigated by explicit types); Kaggle token exposure (kept in `~/.kaggle/` or env, never in the repo); disk space for raw data + Parquet + spill.

### Phase 1: MVP

Scope: EDA, validation, cleaning, partitioning, LR baseline, LightGBM, ROC-AUC, PR-AUC, E1 on application features. MLflow tracking (no registry yet) starts here to avoid building a temporary logger.

**Learn:** imbalanced-classification metrics; stratified partitioning; sklearn `Pipeline` / `ColumnTransformer`; LightGBM missing-value and categorical handling, `lgb.cv` early stopping; paired bootstrap; MLflow tracking basics.

**Tasks:**
1. EDA notebook: missingness, distributions, sentinels, univariate association with `TARGET`, data quality notes.
2. Application validation: Pandera schema plus SQL checks (unique `SK_ID_CURR`, `TARGET ∈ {0,1}`, domains, sentinel counts). Machine-readable validation report.
3. Cleaning module with one test per rule.
4. Partitioning (Section 4) with role-gated loaders and tests.
5. Local MLflow tracking server via `make mlflow` (Section 9.1 settings, host mode).
6. LR pipeline: median imputation + missing indicators, log transform for monetary fields, scaling, one-hot with infrequent-category handling; `C` tuned by inner CV on Train.
7. LightGBM pipeline: native NaN handling; categorical handling decided and documented; rounds from inner CV; small tuned search space.
8. Evaluation module: ROC-AUC, PR-AUC, Brier, log loss, curves; paired bootstrap for differences; prediction files.
9. E1 on application features (Section 6).
10. Leakage diagnostics: permutation test and single-feature screening (Section 7.2).
11. Determinism check: run training twice, compare.

**Deliverables:** EDA notebook; validation report; `docs/leakage.md` v1; E1 section in `docs/experiments.md`; MLflow runs.

**Exit criteria:**
- Two identical runs produce identical metrics (same platform).
- Leakage unit tests pass. The permutation test is within its noise band.
- E1 is reported with CIs and a decision made by the pre-declared rule.
- No code path read Test (verified by the loader test and the ledger being empty).

**Risks:** accidental test use (prevented by loader gate); LR convergence (scaling, solver choice); over-reliance on `EXT_SOURCE_*` (documented as a dependency on opaque external scores).

### Phase 2: Portfolio-ready ML

Scope: multi-table features, reusable pipeline, calibration, threshold, E1 (repeat), E2, E3, E4, selection lock, final Test evaluation, model card, verification.

**Learn:** DuckDB aggregate and window functions, two-level aggregation; leakage patterns in historical data; calibration theory and diagnostics; decision-theoretic thresholds; MLflow artifacts and nested runs.

**Tasks:**
1. Historical-table validation: foreign-key coverage, temporal validity (Section 7.1), sentinel handling, duplicate keys.
2. Feature SQL per group (Section 8), each with fixture-based unit tests, including no-history semantics.
3. Feature registry (`configs/features.yaml`) and generated column lists.
4. Feature build with per-group Parquet outputs and a manifest. Memory profile documented.
5. E2 ablation and leave-one-group-out. Decide the served source set (D2). Freeze contract version 1.0.
6. E1 repeated on the selected feature set.
7. Sensitive-attribute policy analysis and decision (Section 6).
8. E3 calibration (Section 5).
9. E4 threshold and cost sensitivity (Section 5).
10. `risklab lock-selection` and `risklab final-eval` with the ledger (Section 4.3). Run final evaluation once.
11. Subgroup metrics on Test (descriptive only; Phase 4 extends this).
12. Model card: intended use, data, metrics with CIs, calibration, threshold rationale, subgroup results, limitations, failure conditions.
13. Reproducibility: reference manifests, `risklab verify`, `scripts/recompute_metrics.py`, `docs/reproducibility.md`.

**Deliverables:** feature tables; `docs/experiments.md` (E1–E4 with run IDs); `docs/leakage.md` v2; selection lock; final evaluation report; `docs/model_card.md`; verification report.

**Exit criteria:**
- Every feature SQL file has fixture tests, and the no-history semantics test passes.
- Each experiment's decision follows its pre-declared rule, or a documented deviation exists.
- Test was opened exactly once for the locked selection (ledger shows one entry).
- `risklab verify` passes on a fresh run, and `recompute_metrics.py` matches logged metrics.

**Risks:** feature explosion (cap by group, select on Train/CV only); Selection overuse (limited number of candidates, all logged); costs being assumptions (sensitivity analysis); isotonic overfitting (simpler-method rule).

### Phase 3: Portfolio-ready serving and engineering

Scope: MLflow server and registry, promotion gate, FastAPI, Docker, unit and integration tests, deployment documentation.

**Learn:** MLflow registry aliases and proxied artifacts; FastAPI lifespan, dependency injection, error handling; Pydantic v2 discriminated unions (for source status); multi-stage Docker with `uv`; compose health checks.

**Tasks:**
1. Compose stack (Section 9.5) and `.env.example`. Verify Host-header settings with an integration test.
2. Registration: log the calibrated model with signature, requirements, `serving_config.json`, `reference_profile.json`, and model card.
3. Promotion gate on **Selection** (never Test):
   - ROC-AUC ≥ champion − tolerance;
   - log loss and Brier no worse than champion + tolerance;
   - calibration ECE ≤ limit;
   - contract and schema valid;
   - parity test passed;
   - library versions match the API image;
   - lock ID present.
   The gate report is logged as an artifact. On first registration, absolute floors from config apply instead of champion comparison.
4. Alias management CLI: `risklab registry promote | rollback | status`, with the audit log.
5. API (Section 10): contract schemas generated from the feature registry and `serving_config.json`; online feature computation (`features/online.py`) that runs the same cleaning code and the same SQL files against request tables in in-memory DuckDB; model store with pinned version and atomic reload; explanations; error model; request limits; admin API key.
6. Tests: API, contract, integration (compose), parity (`data` marker), golden predictions.
7. `docs/api_contract.md`, `docs/deployment.md` (clone → data → train → register → compose up → predict), `README.md` quick start.

**Deliverables:** running compose stack; registered champion; API with OpenAPI docs; test suite; deployment docs.

**Exit criteria:**
- Following `docs/deployment.md` on a clean machine yields a working `/predict` against the registered champion.
- The parity test passes on real data.
- `not_provided` for a required source returns 422, and `provided` with empty records returns the no-history result. Both are tested.
- Reload and rollback work without restarting the API, and a failed reload keeps the old model serving.
- CI is green.

**Risks:**
- Training-serving skew in cleaning code. Mitigation: shared module, and the parity test covers cleaning.
- Library version drift between training and API images. Mitigation: one lock file plus the load-time check.
- Large payloads. Mitigation: limits.
- MLflow server unauthenticated. Mitigation: bound to localhost; documented.

### Phase 4: Advanced

Scope: simulated drift monitoring, performance monitoring, subgroup analysis, model version rollback, per-loan cost-sensitive threshold, load testing.

**Learn:** PSI, KS, chi-square and why p-values are uninformative at large n; covariate, prior, and concept shift; label delay; subgroup metric uncertainty; Locust.

**Tasks:**
1. Monitoring batch simulator from the Monitoring pool (seeded, registered in `data/monitoring/manifest.json`):
   - control (random) batches;
   - covariate shift (resample toward younger applicants or one income type);
   - upstream bugs (`AMT_INCOME_TOTAL × 1000`, 60% missing `EXT_SOURCE_3`, category renamed);
   - prior or concept shift (higher positive rate in a segment, or label flips);
   - Kaggle `application_test` as a real unlabeled sample.
2. Drift metrics against the model version's `reference_profile.json`:
   - per-feature PSI (numeric and categorical) and KS effect size;
   - score PSI (reference = Selection scores, not Train scores, which are optimistic);
   - missing-rate change.
3. Performance metrics for labeled batches: ROC-AUC, PR-AUC, Brier, ECE, calibration-in-the-large (mean predicted vs observed rate), approval rate and expected cost at the served threshold.
4. Alert thresholds calibrated from control batches: the false-alarm rate at the chosen PSI thresholds and batch size is measured and reported (target ≤ 5% of control batches flagged). Rules live in `monitoring.yaml`.
5. Subgroup analysis on monitoring batches and on the final Test report:
   - groups: gender, age bands, income type, education, region rating, contract type, has-bureau-history;
   - metrics: AUC, Brier, calibration-in-the-large, approval rate, FPR, FNR, with bootstrap CIs;
   - groups below `min_n` or `min_positives` are suppressed.
6. `POST /monitoring/evaluate` plus the equivalent CLI. Reports are written to `reports/monitoring/`.
7. Rollback scenarios:
   - a) A corrupted-data candidate v2 is **blocked** by the gate.
   - b) v2 is force-promoted with a recorded reason to simulate a bad deployment. Monitoring on a labeled batch detects the regression. `risklab registry rollback` restores v1, and the API reloads.
   - c) A drift scenario where both versions degrade, showing that rollback addresses model regressions, not data drift.
8. Per-loan cost model: FN cost ≈ LGD × `AMT_CREDIT`, FP cost ≈ margin × `AMT_CREDIT`. Threshold chosen on Selection with an optional minimum approval-rate constraint. Sensitivity over LGD and margin. If this changes the served threshold, it is a new selection lock, and any Test opening is disclosed as a reuse.
9. Load testing with Locust: application-only payload vs full-history payload, 1 and N workers. Report p50, p95, p99, throughput, and error rate in `docs/monitoring.md` (or `docs/performance.md`). An indicative target (for example p95 < 100 ms for a typical payload on one worker) is validated, not assumed.

**Deliverables:** monitoring module, endpoint, and reports; `docs/monitoring.md`; rollback demo script and audit log; cost analysis; load test report.

**Exit criteria:**
- The control-batch false-alarm rate is ≤ the configured target.
- Each injected fault is flagged on the affected feature(s). Missed detections of subtle shifts are reported, not hidden.
- Rollback scenario (b) is reproducible with one command, and `/models/current` reflects each change.
- The subgroup report identifies where the model is weaker, with CIs.
- Test ledger entries are unchanged unless a reuse was explicitly acknowledged.

**Risks:**
- Simulated drift is too easy to detect. Mitigation: include mild shifts and report the detection limits.
- Fairness misinterpretation: metric gaps are descriptive and not proof of discrimination.
- Admin endpoint exposure. Mitigation: API key, localhost binding.

### Phase 5: Optional

Each item is added only when there is a concrete need:

- Streamlit dashboard that reads `reports/` and calls the API. It contains no ML logic.
- Evidently HTML reports.
- A validated degraded fallback model for `not_provided` sources, with its own registry entry and contract.
- PostgreSQL for prediction logs and monitoring history.
- Optuna for a larger hyperparameter search, still on Train CV only.

---

## 15. Risk register (cross-cutting)

| Risk | Impact | Mitigation |
|---|---|---|
| Test contamination | Optimistic, unverifiable results | Partitions, loader gate, lock, ledger (Section 4) |
| Training-serving skew | Wrong predictions in the API | One SQL and cleaning codebase; parity test |
| Leakage from history tables | Inflated metrics | Temporal validity checks, permutation test, screening (Section 7) |
| Non-determinism | Results not reproducible | Seeds, deterministic settings, tolerances, verify command |
| Memory exhaustion | Pipeline failures | DuckDB memory limit and spill; aggregate before Python; float32 |
| MLflow path or host issues in Docker | API cannot load model | Server with proxied artifacts; allowed-hosts; integration test |
| Overclaiming | Misleading portfolio | Model card, limitations, demo vs production table |

---

## 16. Production-oriented demo vs real production

| Aspect | RiskLab | Real production |
|---|---|---|
| Data | Static Kaggle snapshot; random stratified partitions | Continuous data; out-of-time validation; vintage backtests |
| Labels | Available immediately | Delayed 6–12+ months; early monitoring relies on drift and proxies |
| Population | Approved loans only | Reject inference; controlled experiments on live traffic |
| Features | Client sends raw records | Bureau integrations, feature stores, data contracts with SLAs |
| Deployment | Alias change and in-process reload | Immutable bundles, shadow and canary releases, approvals |
| Governance | Model card and audit log | Model risk management, independent validation, regulatory adverse-action reasons, fair-lending review |
| Security | API key for admin; localhost binding; no real PII | AuthN/AuthZ, encryption, PII handling under law, centralized audit |
| Operations | JSON reports, manual runs | Alerting, on-call, SLOs, high availability, scheduled jobs |
| Tracking backend | SQLite, single writer | PostgreSQL/MySQL plus object storage |

---

## 17. Assumptions and open items

- Exact partition shares are fixed at the start of Phase 1 and never changed afterwards without a full re-run and a disclosed Test reuse.
- `min_gain` values for E1 and E2, the ECE binning, gate tolerances, and cost assumptions are set in config before the corresponding experiments run.
- Default policy is to exclude `CODE_GENDER` as a model feature. This is pending the Phase 2 analysis and owner confirmation.
- Library versions (sklearn ≥ 1.6 for `FrozenEstimator`, MLflow ≥ 3.5 for allowed-hosts behavior, LightGBM 4.x) are pinned in Phase 0 and rechecked against current docs.
