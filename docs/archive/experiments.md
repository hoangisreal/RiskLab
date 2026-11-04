# Experiments

> ARCHIVED 2026-11-04 — superseded by `docs/RESULTS.md`. Kept for full original tables.

These are contract-1.1 results from the HTTP MLflow server. All choices use Selection and were frozen before the owner-authorized final Test evaluation. The final report is described in the model card; no Test result informed these choices. Train/Calibration/Selection/Monitoring roles and the fixed partition hash `b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101` are unchanged. Train has 169,131 applicants and Selection 30,751. Configured comparisons use 1,000 paired resamples and 95% intervals. Decisions D13–D16 were recorded before the accepted runs.

The initial unordered contract-1.1 run failed cross-process candidate verification. Its artifacts are preserved locally; the results below use explicitly ordered floating aggregates. No metric tolerance or selection rule was relaxed.

## E1 on application features

Runs: LR `50e64b81e3464e3da2d0d30caae9d88d`, LightGBM `0ddfcd8a40ac4820b6da16ae205ab826`, comparison `da3c298ee217424e9fd4f2e9ba769906`.

| Selection metric | LR | LightGBM |
|---|---:|---:|
| ROC-AUC | 0.746391 | 0.755967 |
| PR-AUC | 0.235942 | 0.243239 |
| Brier | 0.068291 | 0.067791 |
| Log loss | 0.248925 | 0.246440 |

LightGBM's ROC-AUC gain is 0.009577 with CI [0.005621, 0.013116], clearing the declared minimum gain 0.005 and positive lower bound. Hyperparameters/rounds use inner five-fold Train CV, with preprocessing refitted per fold. This repeats the earlier raw-application baseline; E2's application baseline additionally contains five row-wise ratios and uses fixed LightGBM parameters, so those two AUCs are not a controlled estimate of the ratios' effect alone.

## E2: historical sources

Run `4e467506bab74355b13bca284dacbb87`. Fixed LightGBM parameters; a group is served only when its marginal cumulative ROC-AUC gain is at least 0.003 and its paired interval lower bound is positive.

| Added group | Cumulative AUC | Marginal gain | 95% interval | Serve |
|---|---:|---:|---|---|
| Application + ratios | 0.757348 | — | — | Yes |
| Bureau | 0.761092 | 0.003744 | [0.001147, 0.006449] | Yes |
| Bureau balance | 0.761983 | 0.000891 | [-0.001619, 0.003046] | No |
| Previous | 0.767689 | 0.005706 | [0.002732, 0.008967] | Yes |
| Installments | 0.774311 | 0.006622 | [0.003468, 0.009847] | Yes |
| POS | 0.775338 | 0.001027 | [-0.001168, 0.003170] | No |
| Credit card | 0.777061 | 0.001723 | [-0.000784, 0.004256] | No |

Served set: application, bureau, previous and installments. Cumulative scores include earlier non-served groups; they are not the served model's score. The repeated E1 below evaluates the actual served set.

Leave-one-group-out is descriptive and does not replace the declared cumulative decision rule:

| Removed group | Full minus without AUC | 95% interval |
|---|---:|---|
| Bureau | 0.002110 | [-0.000352, 0.004695] |
| Bureau balance | -0.001093 | [-0.003408, 0.001149] |
| Previous | 0.003961 | [0.001138, 0.006851] |
| Installments | 0.002934 | [-0.000028, 0.005935] |
| POS | -0.000039 | [-0.002137, 0.002194] |
| Credit card | 0.001723 | [-0.000784, 0.004256] |

These effects depend on the other groups present. Several intervals include zero, so the source set is not claimed to be uniquely optimal. The rule prioritizes a bounded source/contract burden over small uncertain gains.

## E1 repeated on the served set

Runs: LR `7e8c455c6d814eedad18a905fca67afe`, LightGBM `cbcfa9885a7e4caa982885b8adbb5cc4`, comparison `2b1c9ef2f9f94984bb0900c54ec96887`. The inputs contain 147 columns. Train CV chose LR C=0.01 and 283 LightGBM rounds (fold best iterations 280/421/238/263/214).

| Selection metric | LR | LightGBM | Difference | 95% interval |
|---|---:|---:|---:|---|
| ROC-AUC | 0.759367 | 0.772521 | 0.013154 | [0.009109, 0.017429] |
| PR-AUC | 0.252196 | 0.263679 | 0.011483 | [0.003368, 0.019703] |
| Brier | 0.067439 | 0.066776 | -0.000663 | [-0.001007, -0.000332] |
| Log loss | 0.245145 | 0.241557 | -0.003588 | [-0.004878, -0.002436] |

LightGBM wins the unchanged rule. This fitted artifact is frozen for E3; it is not replaced with another fit after selection. ROC/PR curves, prediction files and model artifacts are logged to these runs.

## Sensitive-attribute policy

Run `ca1631574f494d1c85c0fbb0e4c97a2f` compares three fixed-LightGBM policies on the served feature set. It is descriptive and does not retune the deployed family.

| Policy | Selection AUC | Change vs default | 95% interval |
|---|---:|---:|---|
| Exclude gender, keep age (default) | 0.772176 | — | — |
| Include gender | 0.775803 | 0.003627 | [0.001206, 0.006057] |
| Exclude gender and declared age inputs | 0.771646 | -0.000531 | [-0.002819, 0.001801] |

Keep `CODE_GENDER` excluded and age included, as declared. A discrimination gain does not resolve the policy question; gender inclusion requires an explicit owner decision. Removing these age inputs does not remove all age proxies. Subgroup comparisons remain descriptive, not a finding about discrimination.

## E3: calibration of the frozen winner

Run `9a84a8cc279846e88b110f06ee3ea69c`, base-model run `cbcfa9885a7e4caa982885b8adbb5cc4`. Calibrators fit only Calibration; comparisons score Selection.

| Method | Log loss | Brier | ECE (15 bins) |
|---|---:|---:|---:|
| None | 0.241557 | 0.066776 | 0.004573 |
| Sigmoid | 0.241619 | 0.066771 | 0.004397 |
| Isotonic | 0.249040 | 0.066894 | 0.003600 |

Choose **none**, the lowest Selection log loss and simplest method. Sigmoid-minus-none log-loss CI is [-0.000064, 0.000196]; isotonic-minus-none is [0.002391, 0.013576]. Sigmoid preserves ranking here; isotonic introduces ties and reduces discrimination. Reliability plots and all method predictions are logged. The explicitly configured class-weight diagnostic is illustrative and never enters selection.

Selected artifact SHA256: `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`. Its metrics reproduce exactly during candidate preparation.

## E4: threshold and proxy costs

Run `92aa4caf17fd434f95ad13dd80de5198` reuses that exact artifact. The base per-loan assumptions are FP cost = 0.05 × credit and FN cost = 0.50 × credit. These are proxies: TARGET is payment difficulty, not measured loss.

The selected global threshold is **0.08040201**. Its Bayes sanity threshold is 0.09090909; the empirical exposure-weighted optimum is 0.010507 lower. At the selected threshold, Selection approval is 0.692238, precision 0.178888, recall 0.682111 and proxy cost per applicant 14,533.40 dataset monetary units. The constant-cost comparison chooses 0.09090909; it does not replace the declared per-loan policy. Curves include both probability endpoints and the Bayes threshold.

| Constant FN/FP ratio | Cost-minimizing threshold |
|---|---:|
| 2 | 0.346734 |
| 5 | 0.160804 |
| 10 | 0.090909 |
| 20 | 0.045226 |

The fixed nine-point LGD/margin sensitivity grid is stored in the same run; its thresholds range from 0.040201 to 0.356784. Assumptions are reported, not chosen for attractive outcomes. The serving bundle freezes them with the selected threshold.

## Diagnostics, controls and historical evidence

Application diagnostics run `51eb3b0ef61747ba93d7582512f88d7e` and served diagnostics run `d7bfa962dc1b44adb0766becfd85a1ab` pass the declared controls; details are in [Leakage](leakage.md). Monitoring-control run `03b14e0ed97c43a48aaaa067ca7eba19` chose scale 1: 1/100 calibration controls and 0/100 held-out controls alerted. This is a static-pool simulation, not a future-population guarantee.

Earlier contract-1.0 runs remain historical: E1 comparison `b3f2b675249b498997102abfca191fe5`, E2 `cf726679c64d467f8ed86c9837fa57d6`, E3 `7296da8d49a744bebf0fc41911a5dce5`, E4 `b24c3fffb0e54c22a354865685703122`. The one historical Test opening belongs to lock `e98453beb10d833e` and does not evaluate contract 1.1. The rejected unordered contract-1.1 experiments are not an accepted baseline. No Test result informed these decisions.
