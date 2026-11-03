# Model card

RiskLab estimates an approved loan applicant's probability of payment difficulties (`TARGET`). This is a production-oriented demo. `approve`/`review` is an illustrative recommendation, not a production credit decision or regulated adverse-action reason.

## Current artifact and policy

Contract 1.1 consumes cleaned application fields/ratios plus bureau, previous-application and installment history. There are 147 model inputs. All predictive preprocessing/category learning occurs inside the Train pipeline. E2 determines the source set; repeated E1 chooses LightGBM, tuned by inner five-fold Train CV. E3 uses that exact frozen artifact and chooses no probability calibration. No fit follows selection.

Artifact SHA256: `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`. Base-model run: `cbcfa9885a7e4caa982885b8adbb5cc4`; calibration/Selection verification run: `9a84a8cc279846e88b110f06ee3ea69c`. Contract-correct historical aggregation combines partial payments, aggregates monthly/installment observations through loans, preserves NULL no-history rates, excludes invalid realized events, and orders floating sums/means deterministically. Public API parity passed on 500 seeded Train applicants.

Gender is excluded. Age is retained under the declared policy; sensitive-attribute analysis run `ca1631574f494d1c85c0fbb0e4c97a2f` is descriptive and does not authorize gender inclusion. Other features can remain proxies. Monitoring subgroup metrics do not establish legal fairness or causality.

## Selection evidence

Repeated-family comparison run `2b1c9ef2f9f94984bb0900c54ec96887` prefers LightGBM over LR: ROC-AUC gain 0.013154, 95% paired CI [0.009109, 0.017429], exceeding the predeclared 0.005 minimum. Source-specific model runs are listed in [Experiments](experiments.md).

| Current artifact, Selection | Value |
|---|---:|
| ROC-AUC | 0.772521 |
| PR-AUC | 0.263679 |
| Brier | 0.066776 |
| Log loss | 0.241557 |
| ECE, 15 equal-frequency bins | 0.004573 |

These metrics come from run `9a84a8cc279846e88b110f06ee3ea69c`. Selection is reused for a limited set of predeclared decisions, so it is mildly optimistic and is not a substitute for a locked Test evaluation. Paired uncertainty for family/calibration comparisons is in the experiment artifacts.

The global reject/review threshold is **0.08040201**, chosen on Selection in run `92aa4caf17fd434f95ad13dd80de5198`. FP cost is assumed margin 0.05 × credit; FN cost is assumed LGD 0.50 × credit. The Bayes reference is 0.09090909. Selection approval is 0.692238 and recall 0.682111 at the chosen threshold, under those illustrative assumptions. Costs use dataset monetary units, not measured realized losses. Fixed LGD/margin sensitivity is reported in that run; no assumption was selected from Test.

## Test evidence and approval status

The corrected artifact has **not been evaluated on Test**. A new content-bound selection lock and explicit owner authorization are required before another opening. Do not attribute the historical result below to this artifact.

Historical contract 1.0: lock `e98453beb10d833e`, recorded model run `cf726679c64d467f8ed86c9837fa57d6`, 46,127 Test rows, one recorded opening. Reported ROC-AUC 0.77320, PR-AUC 0.25114, Brier 0.06735 and log loss 0.24260. That legacy report lacks a separate evaluation run, saved predictions, confidence intervals and subgroup tables. It remains immutable; missing evidence is not retroactively invented. Any authorized new evaluation is disclosed as reuse and must not feed further model choices.

## Population and failure conditions

Only approved Home Credit loans are observed, creating selection/reject-inference bias. `TARGET` means payment difficulty, not default or loss. Relative time fields do not support reliable out-of-time validation; partitions are stratified holdouts with fixed shares/seed. Random-split results do not establish performance on future applicants, other lenders, rejected applications or changed economies.

External scores provide substantial signal and cannot be independently inspected here. Missing or renamed upstream fields, changed source coverage, category shifts, covariate/prior/concept drift and new populations can degrade results. Empty history and unavailable history have different semantics; required unavailable sources are rejected. Explanations describe model contributions, not causal effects.

Monitoring uses a Train-fitted immutable aggregate profile and Selection score/performance reference. Control calibration run `03b14e0ed97c43a48aaaa067ca7eba19` measured 0/100 held-out alerts at the frozen scale after 1/100 calibration alerts. Static batches can overlap; this does not guarantee a production false-alarm rate. Rollback addresses a model regression; shared data/concept drift can affect both versions. Labels are immediate in this simulation, unlike production delay.

The bundle exposes lock/run/artifact/runtime identity, frozen assumptions and parity evidence. Synthetic deployment tests verify exact registered predictions, reload failure safety and rollback validation. Latest real deployment/reproduction/load evidence is tracked separately in [Progress](PROGRESS.md) and [Monitoring](monitoring.md). Production gaps include authentication beyond an admin key, encryption/governance, coordinated multi-worker deployment, live monitoring/on-call and independent validation.
