# Model card

RiskLab estimates the probability that a loan applicant will have payment difficulties (`TARGET`). It is a production-oriented demo, not a production credit-decisioning system; the recommendation (`approve`/`review`) is advisory only.

## Intended use and limits

- Population: approved loans only (reject-inference bias); `TARGET` means payment difficulties, not default or loss.
- All time fields are relative to the application date; there is no absolute date, so partitions are stratified holdouts, not out-of-time validation.
- Published Kaggle AUC values are context only (plan D7), never acceptance criteria.

## Data and features

- Partitions (seed 20261101, shares 55/10/10/15/10): Train fits, Calibration calibrates, Selection selects, Test evaluates once per lock, Monitoring simulates drift.
- Engineered groups: application ratios, bureau, bureau balance, previous applications, installments, POS/cash, credit card (`configs/features.yaml`, `sql/features/`). Cross-applicant statistics are fitted inside the sklearn pipeline on Train only.
- Default policy excludes `CODE_GENDER` from model features; the Phase 2 policy analysis must be recorded before locking.

## Metrics, calibration, threshold

Metrics (ROC-AUC, PR-AUC, Brier, log loss, ECE) are reported with the MLflow run ID that produced them, and the lock ID for Test metrics. Calibration follows plan Section 5 (log-loss choice with the simpler-method rule); the threshold minimises expected cost on Selection under `configs/cost.yaml` and is sanity-checked against the Bayes threshold. Real-data results (all on Selection, Test unopened): E1 LightGBM 0.75597 vs LR 0.74639 (runs c9009e8a…, 9316731e…); E2 served set application+previous+installments (run cf726679c64d467f8ed86c9837fa57d6); E3 calibration chosen: none, log loss 0.24234, ECE 0.00292 (run 7296da8d49a744bebf0fc41911a5dce5); E4 threshold 0.0839 at C_FN/C_FP=10 (run b24c3fffb0e54c22a354865685703122). Served-only Selection ROC-AUC 0.76929, PR-AUC 0.26143, Brier 0.06688. Test metrics will be added with the lock ID after the single locked final evaluation.

## Failure conditions

Expect degradation under covariate/prior/concept shift, corrupted upstream fields (for example income scaled by 1000 or mass-missing external scores), and populations unlike approved Home Credit applicants. Monitoring (PSI/KS, calibration-in-the-large, subgroup gaps) detects drift; rollback fixes model regressions, not data drift.
