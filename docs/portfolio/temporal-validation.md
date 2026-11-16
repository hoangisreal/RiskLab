# Extending evaluation over time

Home Credit 2018 lacks absolute application dates. A stratified holdout does not
establish performance on future applicants. Changing the seed or treating IDs
as a chronological sequence does not resolve this limitation.

## Dataset selected for further research

[Home Credit — Credit Risk Model Stability](https://www.kaggle.com/c/home-credit-credit-risk-model-stability/data)
provides `date_decision`, `WEEK_NUM`, `case_id` and training label `target`.
The official source describes the target as determined after a period based on
loan default. Its data/label contract differs from contract 1.1: do not attribute
2018 models/metrics to 2024 data, join applicant IDs across datasets, or treat
this as external validation with matching populations and features.

Authenticated Kaggle CLI was used to download only training base, two static_0
shards and the feature dictionary, without competition Test. Use is governed by
the [competition rules](https://www.kaggle.com/c/home-credit-credit-risk-model-stability/rules).
Do not accept terms on behalf of the account owner or use mirrors with unclear
permissions. Acquire only required training shards, without duplicating CSV and
Parquet downloads or downloading competition test. Raw data stays outside GitHub.

## Protocol to establish before examining results

1. Inventory time/schema metadata; record manifest hashes, source, permitted
   scope and acquisition time. Keep original data outside Git.
2. Define chronological Train/Calibration/Selection/final holdout partitions.
   The final holdout is a new real Test partition and remains unopened.
3. Establish the observation window, label horizon and label-maturity time.
   If the horizon is insufficiently known, explicitly record that a safe purge
   gap is unconfirmed; do not assume labels exist at application time.
4. Use only application-time information as features. Dates/weeks/IDs support
   partitioning and audits, not predictors by default. Check entity overlap when
   customer identity exists; distinct cases do not establish distinct people.
5. Use expanding-window CV within Train, fitting preprocessing in each fold;
   fix compute budgets and preserve Calibration/Selection roles.
6. Report ROC-AUC, PR-AUC, Brier, log loss, weekly/monthly calibration, case and
   positive counts, and coverage. Do not assign AUC to single-class weeks.
   Uncertainty must account for time dependence, for example week-block bootstrap.
7. Lock artifact/protocol before requesting access to the final holdout. Never
   choose a model or threshold from that holdout; retain degraded results too.

Replacing the application model requires a new contract and evidence. The study
below is not external validation of the 2018 model or production model acceptance.

## E8 — retrospective chronological development

Source: 1,526,659 cases, weeks 0–91. Only week/date metadata was read to define the
protocol; 11 predictors were selected from the dictionary before target analysis.
Raw downloads and manifest hashes are in `data/stability/`; credentials are not
stored in the repository.

| Role | Weeks | Label access |
|---|---|---|
| Train | 0–49 | Up to 300,000 selected by case_id hash without using target |
| Calibration | 54–58 | Labels unopened; no E8 calibrator fitted |
| Selection | 63–68 | Evaluated on 33,400 cases |
| Final Test | 73–81 | Labels unopened |
| Monitoring | 86–91 | Labels unopened |

The first four weeks after each boundary are excluded; this buffer is not
validated against the actual label horizon/maturity. Results are retrospective
development, not evidence of a deployment simulation with correctly timed label
availability. Customer identity is unavailable, so cross-week entity overlap
cannot be excluded. Decision-time predictor availability relies on the source
dictionary and has not received an independent upstream audit.

Two forward folds stay entirely within Train, with preprocessing fitted in each
fold. Models/budgets are fixed without Selection tuning. Bootstrap uses 1,000
week-block resamples to preserve within-week dependence; only six Selection
weeks limit interval precision. Intervals do not cover uncertainty from training
a different model.

| Model | ROC-AUC | PR-AUC | Brier | Log loss |
|---|---:|---:|---:|---:|
| Logistic Regression | 0.615878 | 0.058438 | 0.031948 | 0.143065 |
| LightGBM | 0.583525 | 0.048628 | 0.032224 | 0.146721 |
| XGBoost CUDA | 0.584143 | 0.048113 | 0.032198 | 0.146475 |

Signal remains weak with 11 predictors. Logistic Regression outperforms the
boosting pipelines tested within this scope; configurations were not changed
after viewing results. LightGBM/XGBoost have higher Brier and log loss than LR
in this run. Do not directly compare this AUC of 0.616 with Home Credit 2018's
0.775: populations, labels, features and splits differ. The LR 95% block AUC
interval is [0.601358; 0.641792]; LightGBM's is [0.562357; 0.598719]. These numbers
must not be used to make lending decisions.

The current run contains three models without Dummy. Run
`5ce5550e61724c478e3e3fb0a003ad3d`, config SHA256
`8b11ac160573dd9b8c30545e277c5991c40460e493c5e1a93382e7adbb88be77`, source SHA256
`af9a6a5bf869f1fc72248044699bb6f268c7edae1eb7a69c8ddc3bd8920e59a2`.
Independent reload and sklearn recomputation of all three artifacts yielded
maximum metric difference 0, reopening only Selection for this check. The final
holdout was not read.

## Reproducing acquisition and the benchmark

Kaggle CLI requires an account with dataset access; never place tokens in commands,
notebooks or logs. These commands download training-only files from the official
source:

```bash
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_base.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_static_0_0.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f parquet_files/train/train_static_0_1.parquet -p data/stability/raw --quiet
kaggle competitions download home-credit-credit-risk-model-stability -f feature_definitions.csv -p data/stability/raw --quiet
uv run python scripts/prepare_stability.py --data-root data/stability
uv run python scripts/portfolio_run.py --study e8 --data-root data/stability \
  --out reports/workspaces/fresh-e8
```

Acquisition was checked with Kaggle CLI 2.2.4. Preparation extracts only the
requested training files, hashes sources and creates a manifest without reading
`target`. An existing manifest cannot be overwritten with different bytes; use
a fresh data root for another revision.

Aggregate evidence: [benchmark evidence](benchmark-evidence.json).
