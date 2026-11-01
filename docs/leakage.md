# Leakage controls and diagnostics

Scope: the application-only model (Phase 1). Historical-table checks are added in Phase 2. The
rules come from `docs/MASTER_PLAN.md` Section 7. Published AUC benchmarks are not used as
evidence in either direction.

## Controls by construction

| Rule | Where it is enforced | Test that would fail on a violation |
|---|---|---|
| Labeled rows are read only through `risklab.data.loaders.load_partition`, which never returns Test | `src/risklab/data/loaders.py` | `tests/unit/test_loaders_isolation.py` (AST scan of `src/risklab/`, Test-role rejection) |
| Partitions are disjoint, stratified and hash-verified on every load | `src/risklab/data/partition.py`, loaders | `tests/integration/test_partition_synthetic.py`, `test_loaders_synthetic.py` |
| Cleaning is strictly row-wise, so it is safe before splitting | `src/risklab/data/clean.py` | `tests/unit/test_clean.py` (whole table equals concatenated chunks) |
| Medians, scaler moments, one-hot and LightGBM category sets are learned inside `.fit` on Train only | `src/risklab/models/pipelines.py` | `tests/integration/test_pipelines_leakage.py` (fitted state equals independently computed Train statistics; control fit on Train plus extra rows differs) |
| Inner-CV preprocessing is refitted on every fold | `src/risklab/models/train.py` | covered by the pipeline tests, since folds use the same pipeline objects |
| Model, feature and threshold decisions use Selection, never Test | E1 runner reads Train and Selection only | `tests/integration/test_e1.py::test_selection_is_evaluated_not_train_or_test` |

## Diagnostics (real data, MLflow experiment `risklab-diagnostics`, run `86bbc74314ec4ca59a46bb9016eb977f`)

Configuration was declared in `configs/experiments/diagnostics.yaml` before the run (decision D12).
Partition hash `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101`.

### Label permutation

Labels are shuffled over Train and Selection by row id, each pipeline is fitted on Train and scored
on Selection. With honest partitions the AUC must stay within 0.5 +/- 0.02.

| Pipeline | AUC per repeat | Result |
|---|---|---|
| Logistic Regression | 0.5026, 0.5048, 0.4949 | within band |
| LightGBM | 0.4966, 0.4995, 0.5019 | within band |

Power control: evaluating on 20% of the Train rows themselves, with permuted labels, gives AUC 0.847
and is detected (outside the band). Without this control a flat result could also mean the test
cannot see leakage at all.

Limit of this test: it detects evaluation rows or evaluation labels reaching the fit. It does not
detect a feature that is itself a copy of the true label, because permuting labels breaks that
copy. That case is covered by the screening below.

### Single-feature screening (Train, 120 inputs)

Threshold for investigation: |AUC - 0.5| > 0.20, except `EXT_SOURCE_*`.

- No feature exceeds the threshold. The strongest are `EXT_SOURCE_3` (0.321), `EXT_SOURCE_1` (0.335)
  and `EXT_SOURCE_2` (0.343), a deviation of 0.16 to 0.18 from 0.5. The next features are at 0.45
  to 0.58 (`DAYS_BIRTH`, `DAYS_EMPLOYED`, `OCCUPATION_TYPE`, `NAME_INCOME_TYPE`,
  `ORGANIZATION_TYPE`, `FLOORSMAX_AVG`, `LIVINGAREA_*`).
- `EXT_SOURCE_*` are scores from external sources and carry most of the signal. The model depends
  on a component RiskLab cannot inspect.
- Categorical AUCs use in-sample level rates and are optimistic for high-cardinality columns
  (`ORGANIZATION_TYPE` has 57 levels, `OCCUPATION_TYPE` 18).
- Missingness is informative (`OWN_CAR_AGE` 66% null, `EXT_SOURCE_1` 56% null). The models use
  missing-value indicators (LR) or native NaN handling (LightGBM), which is legitimate at
  application time.

## Not covered yet

- Historical tables (bureau, previous applications, installments): temporal validity, orphan keys
  and duplicate keys are documented in `docs/data_contract.md` but not yet handled. Phase 2.
- Leakage through the selection process itself (reusing Selection for several decisions) is
  limited by the pre-declared rules and mitigated by the single-use Test partition.
- The diagnostics describe this pipeline on this data. They are not a proof of absence of leakage.
