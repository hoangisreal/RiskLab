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

The corrected engine, controls, real scenarios, rollback exercise and load matrix are verified below. Historical untracked rollback/load figures are not presented as evidence for this implementation. Labels are immediately available in these simulations; delayed production-label workflows remain outside this demo. New contract-1.1 Test evidence remains approval-dependent.

## Accepted real operational evidence

Scenario-summary run `f1d5df3f91014aaebe595dfaab7ed6b3` evaluates pinned version 4 on seeded 2,000-row batches. The ordinary control has no alert (run `33a3e767bd284070929c04106f841cd6`). Mild age resampling is missed at the declared thresholds (run `fd049569028b4136aebcf66a44e05862`); this is the observed detection limit, not a reason to tune on the validation batch. Strong age shift alerts on age/employment/income features (run `1272da87f37c4f1f805fc21ffd4b9940`).

| Injected condition | Detected signal | Source run |
|---|---|---|
| Income ×1000 | Income and all affected income-ratio features | `ea41af7ca049477ea5554b7face0e749` |
| 60% EXT_SOURCE_3 missing injection | EXT_SOURCE_3 missingness/distribution | `f2f42cacb5604298a7d5504dcbd3d969` |
| Income category renamed | NAME_INCOME_TYPE distribution | `33b4f72b3a3e4cc8a98965cd777b634a` |
| 10% label flips | AUC/log-loss/Brier regression; no feature/score drift alert | `3b6813c849f6470bb7444119302e3b9e` |
| Real unlabeled Kaggle sample | Bureau/request-count feature differences; no performance section | `4e8589d56a9d4a2681d5fc3dd671e760` |

Subgroup examples from control run `33a3e767bd284070929c04106f841cd6`:

| Group | Rows / positives | AUC [95% CI] | FNR [95% CI] |
|---|---|---|---|
| Pensioner | 355 / 20 | 0.632 [0.536, 0.714] | 0.800 [0.619, 0.956] |
| Age 55–65 | 387 / 26 | 0.657 [0.545, 0.756] | 0.654 [0.462, 0.838] |
| Male | 689 / 66 | 0.748 [0.679, 0.817] | 0.273 [0.170, 0.388] |

These overlapping groups have wide uncertainty and multiple comparisons. They identify descriptive weaknesses, especially recall among the observed pensioners/older applicants; they do not authorize a group-specific threshold or establish discrimination.

The one-command rollback run `1f82bddd7d374a7181a57445f65c9bc9` blocked diagnostic version 5, force-promoted it with the declared reason, detected its regression and restored version 4 through CLI rollback/API reload. Control AUC was 0.76774 for version 4 (run `26b435bb3c2042c09f45cab78cebcbbf`) and 0.48387 for version 5 (run `70301bd4a0dd422a8d336d009d3475a4`). On shared concept shift, both degraded and alerted: AUC 0.61169/0.49064 (runs `6ec9f4f1ca834623bc3a935083f5245d` / `8bdb57ccfaf04b22b946aaa6767af11d`). The API and alias returned to version 4; the Test journal was unchanged.

## Measured load and limits

The declared concurrency-eight matrix uses 200 measured requests/case, 20 excluded warmups, a 2,258-byte empty-history payload and an 11,442-byte full-history payload (4 bureau, 3 previous, 26 payment records). The host has 16 logical CPUs and approximately 15.3 GiB RAM; the API uses the pinned Linux/Python image. Other host activity can affect these local observations.

| Workers | Payload | p50 / p95 / p99, ms | Throughput, req/s | Errors |
|---|---|---|---:|---:|
| 1 | Empty history | 714.8 / 834.3 / 898.4 | 11.10 | 0/200 |
| 1 | Full history | 726.2 / 859.3 / 876.6 | 10.86 | 0/200 |
| 4 | Empty history | 247.9 / 351.9 / 971.5 | 29.28 | 0/200 |
| 4 | Full history | 279.6 / 351.3 / 411.1 | 29.94 | 0/200 |

Source load runs: one worker `666d3ba6f62541efa8892ddf68aa64d8`; four workers `2e384c5b561f4186ada07a39bac466ed`. All responses retained the pinned version. The API was restored to one worker after the matrix.

A separately labeled single-client diagnostic used the same payloads/model: empty-history p95 115.4 ms and full-history p95 131.0 ms, with 0/200 errors each (run `41cc517ba01145b38cf4893d372abb1b`). The indicative 100 ms target is not met, even without concurrent load. Four workers improve throughput, but no production latency guarantee is claimed. Per-request feature construction and model preprocessing remain a performance limit; changing the evaluated artifact or expanding infrastructure to hide that result is outside this evidence.
