# Leakage controls and diagnostics

> ARCHIVED 2026-11-04 — summary in `docs/RESULTS.md` §6. Kept for the full controls table.

Scope: both the raw application baseline and contract-1.1's served application/bureau/previous/installment set. Published benchmark AUCs are contextual and do not establish leakage or acceptance.

| Control | Enforcement and evidence |
|---|---|
| Partition roles and Test isolation | `load_partition` rejects Test for every purpose before reading config/data. The AST guard permits the Test role file only in partition construction/verification and locked final evaluation, and distinguishes unlabeled Kaggle input. Role coverage/disjointness, hashes and mutation guards are tested. |
| Train-only learned transforms | Pipeline tests independently verify imputer/scaler/category state against Train statistics; changing other partitions cannot affect fitted state. Every inner CV fold fits its own preprocessing. |
| No cross-applicant feature statistics or labels in SQL | Feature SQL uses only the applicant's history and contains no TARGET reference. Hand-calculated fixtures cover every output, missing history, partial payments and other-applicant isolation. |
| Historical validity | Shared configured cleaning excludes future realized/missing required events; sentinels/categories are handled row-wise. Duplicate/conflicting history keys fail validation. Batch and public API reproduce the same SQL/cleaning. |
| Deterministic values | Floating aggregates have explicit value ordering. Row-permutation/thread-count regression and real public-schema parity prevent tiny aggregate changes from crossing tree splits. |
| Frozen calibration and serving | Calibrators fit Calibration only around the frozen E1 winner. Selection chooses the method/threshold; persisted E3/E4 hashes and no-refit tests bind subsequent preparation/registration. |
| Final evaluation | Clean-tree content lock, data/runtime/protocol verification, an exclusive guard and a durable pre-read journal. Completed results are idempotent; any new opening requires explicit recorded reuse. |

Real historical validation found no fatal key conflicts or future realized events. It excluded 2,905 payment records with missing `DAYS_ENTRY_PAYMENT`. The 653,483 excess installment-key rows are partial payments, not identical duplicates; they are consolidated before comparing amounts. Orphan child records with applicant IDs remain attributable by loan; bureau-balance records without a valid bureau parent cannot enter features. Counts come from the unlabelled historical-table validation manifest, not target association analysis.

## Declared diagnostics and accepted runs

Application run `51eb3b0ef61747ba93d7582512f88d7e`; served run `d7bfa962dc1b44adb0766becfd85a1ab`. The same config declared a permutation band ±0.02 around 0.5, univariate screening deviation 0.20, maximum single-feature gain share 0.50 and absolute Train-CV/Selection AUC gap 0.05 before these runs.

| Inputs | LR permutation AUCs | LightGBM permutation AUCs | Overlap power-control AUC |
|---|---|---|---:|
| Application | 0.50264 / 0.50482 / 0.49494 | 0.49663 / 0.49950 / 0.50193 | 0.847 |
| Served | 0.50234 / 0.50342 / 0.50118 | 0.48814 / 0.49690 / 0.49748 | 0.86396 |

All disjoint evaluations stay in band, while evaluating permuted Train rows themselves is detected. Neither input set has an unexplained single-feature screen flag. On the served model, the largest gain share is 0.15543 (`EXT_SOURCE_3`) and the Train-CV minus Selection AUC gap is -0.00440, both below their declared thresholds (served diagnostic run above; source model run `cbcfa9885a7e4caa982885b8adbb5cc4`). Importance is descriptive split gain, not causal importance.

The external scores remain a dependency: univariate Train AUCs are 0.32061/0.33476/0.34261 for EXT_SOURCE_3/1/2 in served run `d7bfa962dc1b44adb0766becfd85a1ab`. Direction can be reversed, so deviation from 0.5 matters. The scores' construction cannot be audited here. Categorical screening uses in-sample level rates and is optimistic for high-cardinality fields; category count and null rate are recorded.

## Limits

Permutation detects evaluation-row/label contamination that survives label randomization. It does not prove that every original feature is causally valid; a copied true label is handled by screening/feature provenance rather than permutation alone. Screening, power controls, temporal checks and parity complement one another and are not a proof of zero leakage.

Selection is reused for a small, predeclared set of choices. The corrected artifact completed its separately authorized locked Test evaluation: run `a9fc07a3cbaf43ab88d450ed6a3eca58`, lock `402b9e050159762d`, opening two overall and one for this lock. Model/feature/calibration/threshold decisions were already frozen and remain unchanged. Historical contract-1.0 Test results are preserved and were not used to select these corrections. No reliable absolute application date exists, so random stratified holdouts do not demonstrate future temporal generalization.
