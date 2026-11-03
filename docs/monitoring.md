# Monitoring

Monitoring is a seeded simulation on the disjoint Monitoring pool. It does not supply training, feature-selection, calibration or threshold decisions. The real Kaggle `application_test` file is a separate unlabeled drift sample; it is never confused with the labeled Test partition.

`risklab monitoring create --batch-id ID --scenario SCENARIO` registers a scenario, seed and row budget in `data/monitoring/manifest.json`. The API accepts that registered ID, never a file path. CLI and API evaluate the requested pinned version in a temporary model store without changing the served model. Reports are stored locally and logged to the HTTP MLflow server with model/Selection/lock identities.

Supported scenarios are `control`, `covariate_young`, `covariate_young_mild`, `bug_income_x1000`, `bug_ext3_missing`, `bug_category_renamed`, `label_flip` and `kaggle_unlabeled`. Fault strengths and batch size are declared in `configs/monitoring.yaml`. An income mutation recomputes all application ratios through the shared SQL. Kaggle reports have drift and score diagnostics only: no performance metric is invented without labels.

The immutable reference profile contains numeric boundaries/CDF values, categorical probabilities and missing rates fitted on Train inside an sklearn Pipeline. Its score distribution and performance baseline describe Selection. Registration logs its exact bytes; a pinned loader checks hash, model identity, contract, columns and Selection aggregates. Monitoring verifies dataset/partition/config provenance before using it. Numeric KS is an effect size on a frozen quantile grid, an approximation to the exact empirical KS; that definition appears in each report. PSI includes shifted tails and unseen categories. Missingness changes participate in alerts.

Labeled reports add ROC-AUC, PR-AUC, Brier, log loss, ECE, calibration-in-the-large, approval/precision/recall and proxy cost under the bundle's frozen policy. Ordinary bootstrap intervals support relative performance-regression alerts and absolute floors. Subgroups include gender, configured age bands, income, education, region, contract and bureau-history availability even when bureau is excluded from the predictive feature set. AUC, Brier, calibration-in-the-large, approval, FPR and FNR have bootstrap intervals; groups below the configured size/positive counts are suppressed. Gaps are descriptive, not a fairness conclusion.

## Control calibration

The declared design uses 100 calibration and 100 held-out control batches, each drawn with an independent seed from one static pool. Batches can overlap, so the measured false-alarm fraction is not a guarantee about independent future production populations. Both feature/score drift and performance alerts participate; subgroup tables are descriptive and do not trigger alerts.

```bash
docker compose run --rm train monitoring calibrate --candidate reports/models/candidate.json
# Or use a compatible registered version:
docker compose run --rm train monitoring calibrate --version VERSION
```

The smallest of the declared scales `[1, 1.25, 1.5, 2]` meeting the 5% calibration target is frozen before the held-out measurement. The resulting policy is bound to the model/profile/config hashes and recorded with its MLflow run ID. A failed held-out policy cannot be used by the runtime. The earlier score-only ten-control experiment does not establish the complete engine's false-alarm rate and is not used as acceptance evidence.

## Rollback exercise

```bash
# Start on the verified candidate, with one API worker and the admin key in the environment.
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run python scripts/rollback_demo.py
```

The script fits a separate diagnostic artifact on permuted Train labels under `configs/experiments/rollback.yaml`. It verifies full parity, locks/registers it, demonstrates the metric gate blocking promotion, then force-promotes it with the configured reason. Monitoring must detect the corrupted deployment. The `finally` block restores the starting version through the documented rollback CLI and reloads the API. A concept-shift batch is then evaluated on both pinned versions to show the limits of rollback. No Test opening is involved. Every alias change is audit-logged, and diagnostic bundles expose their purpose.

## Load measurement

`configs/load_test.yaml` declares 200 measured requests, 20 excluded warmups, concurrency 8 and a 1/4-worker matrix. `scripts/load_test.py` uses the existing httpx dependency and reports p50/p95/p99, throughput, errors and payload size with a load-test MLflow run ID. The full-history case is a seeded representative Train payload using the public contract; the empty-history case keeps the same application and explicitly empty required sources. Raw payloads are not logged.

```bash
RISKLAB_API_WORKERS=1 docker compose up -d api
uv run python scripts/load_test.py --workers 1
RISKLAB_API_WORKERS=4 docker compose up -d api
uv run python scripts/load_test.py --workers 4
```

Reload is an in-process operation. Use one worker for the rollback exercise; a multiple-worker production deployment would need coordinated reload or process replacement. No alias changes are made during the load matrix. Latency targets are observations to verify on the recorded hardware, not claimed service guarantees.

## Evidence status

The corrected engine, controls protocol and rollback/load commands are implemented. Contract-1.1 real-data experiments and operational verification are in progress; consult `PROGRESS.md` and the completion audit for completed checks. Historical untracked rollback/load figures are not presented as evidence for this implementation. Labels are immediately available in these simulations; delayed production-label workflows remain outside this demo.
