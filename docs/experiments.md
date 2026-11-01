# Experiments

Every number below comes from an MLflow run on the local tracking server (`make mlflow`). Run ids
are listed with each result. Decision rules, `min_gain` and bootstrap settings were declared in
`configs/experiments/*.yaml` before the runs (decision D12 in `docs/PROGRESS.md`). The Test
partition has not been opened (ledger entries: 0).

Common setup: partition hash `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101`
(seed 20261101, shares 55/10/10/15/10). Models are fitted on Train (169,131 rows, 13,654 positives)
and compared on Selection (30,751 rows, 2,482 positives). Hyperparameters and boosting rounds come
from 5-fold stratified CV inside Train, with preprocessing refitted on every fold.

## E1: Logistic Regression vs LightGBM (application features only)

Question: does LightGBM beat the Logistic Regression baseline on the same 120 cleaned application
features, enough to justify the extra complexity?

Rule (declared in advance): prefer LightGBM only if the ROC-AUC gain is at least 0.005 and the lower
bound of the 95% paired-bootstrap interval is above 0. Otherwise prefer Logistic Regression.

MLflow experiment `risklab-e1-model-family`. Runs: Logistic Regression
`c9009e8af3934030b284ada930067ae9`, LightGBM `9316731e8edb4dc583f54fb3a5c118f9`, comparison
`b3f2b675249b498997102abfca191fe5`.

### Inner CV on Train (selection of C and rounds)

| Model | Setting | Result |
|---|---|---|
| Logistic Regression | C = 0.01 / 0.1 / 1.0 | mean AUC 0.74508 / 0.74550 / 0.74539 (std about 0.0046); C = 0.1 chosen |
| LightGBM | early stopping on each fold | best iterations 212, 189, 259, 169, 213; 208 rounds chosen; fold AUC 0.7450 to 0.7552 |

The three C values differ by less than the fold standard deviation, so the choice of C matters
little.

### Selection results

| Metric | Logistic Regression | LightGBM | Difference (LightGBM - LR) | 95% paired bootstrap interval |
|---|---:|---:|---:|---|
| ROC-AUC | 0.74639 | 0.75597 | +0.00958 | [+0.00562, +0.01312] |
| PR-AUC | 0.23594 | 0.24324 | +0.00730 | [+0.00017, +0.01495] |
| Brier score | 0.06829 | 0.06779 | -0.00050 | [-0.00078, -0.00022] |
| Log loss | 0.24892 | 0.24644 | -0.00249 | [-0.00351, -0.00135] |

Prevalence on Selection is 8.07%, so a random PR-AUC would be about 0.081.

### Decision

LightGBM is preferred under the pre-declared rule: the ROC-AUC gain of 0.00958 exceeds 0.005 and
the interval excludes 0. Probabilities are uncalibrated at this stage, so Brier and log loss
differences describe raw model output only. Calibration is the next experiment (E3).

Caveats:
- The gain is modest. The lower bound of the interval (0.0056) is only just above `min_gain`, so
  the preference for LightGBM is not strong evidence of a large difference.
- Both models depend on `EXT_SOURCE_*`, external scores that RiskLab cannot inspect
  (`docs/leakage.md`).
- Selection is a single random split of one dataset. It supports a comparison between these two
  models, not a claim about future performance.
- Output sizes: Logistic Regression uses 273 encoded features, LightGBM 120.
  Final fits took 11.7 s (LR) and 6.5 s (LightGBM).

### Verification

- `scripts/recompute_metrics.py reports/experiments/e1` recomputes all eight metrics from the
  prediction file with plain scikit-learn and reproduces them to 1e-9.
- A second full run (without MLflow) reproduced the inner-CV results, selection metrics, bootstrap
  intervals and decision exactly, on the same machine and library versions.
- Library versions: pandas 3.1.0, scikit-learn 1.9.1, LightGBM 4.7.0, MLflow 3.17.2.

## Leakage diagnostics

MLflow experiment `risklab-diagnostics`, run `86bbc74314ec4ca59a46bb9016eb977f`. Results and
interpretation are in `docs/leakage.md`. Summary: the label-permutation AUC stayed within
0.5 +/- 0.006 for both models, the positive control was detected (AUC 0.847), and no feature
exceeded the screening threshold.

## Not run yet

- E2 (feature groups), E3 (calibration), E4 (threshold and business cost): Phase 2, after
  historical features exist.
- The Test partition: opened only by `final-eval` with a selection lock, which does not exist yet.
