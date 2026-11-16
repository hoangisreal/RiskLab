# Extended model comparison — E7

This study compares common tabular risk-classification pipelines. E7 broadens
E5/E6 rather than starting a new model-selection/Test cycle. Configurations were
declared in `experiments/e7.yaml`; five baselines are reused unchanged from
`experiments/e5.yaml`, and additional models have fixed budgets.

| Model family | Role | Execution |
|---|---|---|
| Logistic Regression | Linear baseline | CPU |
| Decision Tree | Single tree with an inspectable structure | CPU |
| Random Forest | Tree bagging | CPU, 4 fit threads |
| Extra Trees | Randomized tree ensemble | CPU, 4 fit threads |
| AdaBoost | Shallow-tree boosting | CPU |
| Gaussian Naive Bayes | Generative baseline assuming conditional independence | CPU |
| MLP | Small tabular neural baseline | CPU, early stopping within Train |
| Linear SVM + sigmoid | Margin classifier with separate probability calibration | CPU |
| HistGradientBoosting | Histogram gradient boosting | CPU |
| LightGBM | Current serving champion's protocol | CPU |
| XGBoost | Boosted-tree challenger | CUDA |
| CatBoost | Boosted-tree challenger | CUDA |

All models use the same 147 served inputs and Train/Selection partitions.
LightGBM retains its native categorical pipeline; other families use the existing
LR preprocessing (Train-only imputation/scaling/one-hot encoding). Preprocessing
and search budgets differ, so this compares application pipelines rather than
establishing an optimal model family. No class weighting/resampling or tuning
was introduced after examining Selection.

The SVM estimator is fitted on Train and then frozen; its sigmoid calibrator is
fitted on Calibration, never Selection. Other models retain raw probabilities.
MLP/HistGB early stopping happens within Train, without Selection. RF/ExtraTrees
prediction uses one thread to preserve floating-point reduction order; tolerances
were not relaxed to pass exact reload checks. Training still uses four threads.

```bash
uv run python scripts/portfolio_run.py --study e7 \
  --out reports/workspaces/fresh-e7 \
  --baseline reports/workspaces/fresh-baseline/baseline
```

Primary metrics are ROC-AUC, PR-AUC, Brier and log loss; the report also records
ECE, fit/batch-scoring time, convergence warnings and artifact hashes. Paired AUC
bootstrap uses 1,000 resamples; a descriptive flag requires gain ≥ 0.005 and a
positive lower confidence bound. Selection has been reused, without multiplicity
correction; this ranking does not authorize promotion. Local batch timings are
not HTTP latency measurements or production SLOs.

## Results

Initial run `baaf8585b3134e36b924610551159b6f` was stopped to fix execution
reproducibility: synthetic parallel RF prediction differed by 2.22e-16 because
of floating-point summation order. Previous output was retained and execution
restarted with a new configuration, without changing hyperparameters to select
against Selection.

The current study has 12 models after the owner requested removal of Dummy;
older runs remain local. Completed run `05a1da3d6f3c4900ab0ebe12a119cdcf` uses
169,131 Train / 30,752 Calibration / 30,751 Selection rows.
Config SHA256 `b764f2d96ca050450c7f3dde444ba6a90fcba5d638fb104653c8b4ed901a3e87`;
source SHA256 `00837cf0688d6a43c02112d6bb2700bf339e256c19517bde32ad13d2ef02ae4e`.

| Model | ROC-AUC | PR-AUC | Brier | Log loss | Fit (s) |
|---|---:|---:|---:|---:|---:|
| xgboost | 0.774287 | 0.270511 | 0.066475 | 0.240619 | 15.2 |
| lightgbm | 0.772521 | 0.263679 | 0.066776 | 0.241557 | 7.5 |
| catboost | 0.772383 | 0.269580 | 0.066586 | 0.241300 | 11.5 |
| hist_gradient_boosting | 0.770225 | 0.263746 | 0.066803 | 0.242090 | 107.2 |
| linear_svm_sigmoid | 0.759739 | 0.249534 | 0.067535 | 0.245348 | 72.2 |
| logistic_regression | 0.759367 | 0.252196 | 0.067439 | 0.245145 | 12.4 |
| random_forest | 0.756660 | 0.251009 | 0.069112 | 0.251861 | 29.9 |
| mlp | 0.754668 | 0.245513 | 0.067816 | 0.247441 | 16.4 |
| extra_trees | 0.737901 | 0.226715 | 0.070536 | 0.258857 | 34.8 |
| adaboost | 0.721792 | 0.197146 | 0.081209 | 0.309825 | 225.7 |
| decision_tree | 0.713672 | 0.194541 | 0.070266 | 0.258565 | 15.8 |
| gaussian_nb | 0.513622 | 0.082817 | 0.887754 | 30.318458 | 6.1 |

XGBoost leads this table, but its AUC gain is only +0.001766, with paired 95% CI
[-0.000834; +0.004528], below the rule. No model was flagged superior; the
champion was retained and tuning was not expanded after examining Selection.

GaussianNB is severely miscalibrated in this pipeline (Brier 0.887754, log loss
30.318458). These poor results are retained and are not used for decisions;
they do not establish that the family performs poorly on other datasets.
AdaBoost has AUC 0.722 but weak probability metrics: AUC alone cannot establish
PD acceptance. Additional models were not tuned to the same extent as the
champion, so this is not a general conclusion about algorithms.

Independent verification in a separate Python process reloaded all 12 artifacts
by SHA256, predicted Selection and recomputed metrics directly with sklearn:
maximum metric difference 0. Every model passed the exact reload probe, with
no ConvergenceWarning in the completed run. Outputs/models/per-row predictions
remain local at `reports/workspaces/portfolio-e7-no-prior-20261116/study/` and
are not committed or published.

Machine-readable aggregate evidence: [benchmark evidence](benchmark-evidence.json).
