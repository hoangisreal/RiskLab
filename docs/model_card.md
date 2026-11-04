# Model card

RiskLab estimates an approved loan applicant's probability of payment difficulties (`TARGET`). This is a production-oriented demo. `approve`/`review` is an illustrative recommendation, not a production credit decision or regulated adverse-action reason.

## Current artifact and policy

Contract 1.1 consumes cleaned application fields/ratios plus bureau, previous-application and installment history. There are 147 model inputs. All predictive preprocessing/category learning occurs inside the Train pipeline. E2 determines the source set; repeated E1 chooses LightGBM, tuned by inner five-fold Train CV. E3 uses that exact frozen artifact and chooses no probability calibration. No fit follows selection.

Artifact SHA256: `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`. Base-model run: `cbcfa9885a7e4caa982885b8adbb5cc4`; calibration/Selection verification run: `9a84a8cc279846e88b110f06ee3ea69c`. Contract-correct historical aggregation combines partial payments, aggregates monthly/installment observations through loans, preserves NULL no-history rates, excludes invalid realized events, and orders floating sums/means deterministically. Public API parity passed on 500 seeded Train applicants.

Gender is excluded. Age is retained under the declared policy; sensitive-attribute analysis run `ca1631574f494d1c85c0fbb0e4c97a2f` is descriptive and does not authorize gender inclusion. Other features can remain proxies. Monitoring subgroup metrics do not establish legal fairness or causality.

## Selection evidence

Repeated-family comparison run `2b1c9ef2f9f94984bb0900c54ec96887` prefers LightGBM over LR: ROC-AUC gain 0.013154, 95% paired CI [0.009109, 0.017429], exceeding the predeclared 0.005 minimum. Source-specific model runs are listed in [Results](RESULTS.md).

| Current artifact, Selection | Value |
|---|---:|
| ROC-AUC | 0.772521 |
| PR-AUC | 0.263679 |
| Brier | 0.066776 |
| Log loss | 0.241557 |
| ECE, 15 equal-frequency bins | 0.004573 |

These metrics come from run `9a84a8cc279846e88b110f06ee3ea69c`. Selection is reused for a limited set of predeclared decisions, so it is mildly optimistic and is not a substitute for a locked Test evaluation. Paired uncertainty for family/calibration comparisons is in the experiment artifacts.

The global reject/review threshold is **0.08040201**, chosen on Selection in run `92aa4caf17fd434f95ad13dd80de5198`. FP cost is assumed margin 0.05 × credit; FN cost is assumed LGD 0.50 × credit. The Bayes reference is 0.09090909. Selection approval is 0.692238 and recall 0.682111 at the chosen threshold, under those illustrative assumptions. Costs use dataset monetary units, not measured realized losses. Fixed LGD/margin sensitivity is reported in that run; no assumption was selected from Test.

## Locked Test evidence

Final evaluation run **`a9fc07a3cbaf43ab88d450ed6a3eca58`**, lock **`402b9e050159762d`**, evaluated 46,127 applicants using the unchanged artifact above and clean source revision `98f5ab3`. The owner approved this reuse on 2026-11-04 after all model, feature, calibration and threshold decisions were frozen. This is the second Test opening overall and the only opening for this lock. The acknowledgment, durable journal, predictions, manifest and aggregate report are retained locally and in MLflow; Test results caused no further selection.

| Test metric | Value | 95% CI |
|---|---:|---|
| ROC-AUC | 0.775432 | [0.767577, 0.782915] |
| PR-AUC | 0.256541 | [0.245630, 0.269374] |
| Brier | 0.067150 | [0.066568, 0.067690] |
| Log loss | 0.241822 | [0.239487, 0.244109] |

Intervals use the locked 1,000-resample stratified percentile bootstrap. At the unchanged threshold 0.08040201, approval is 0.691569, precision 0.179940, recall 0.687433, FPR 0.275146 and FNR 0.312567. Proxy cost is 14,772.60 dataset monetary units per applicant under the frozen LGD/margin assumptions. ECE is 0.007156 and calibration-in-the-large is -0.001082. All Test figures in this section belong to the run/lock above.

The report covers all seven required subgroup dimensions: gender, age band, income, education, region, contract and bureau-history availability. It publishes 23 of 28 levels; five levels are suppressed under the locked minimum 200 rows / 20 positives. Subgroup intervals use 1,000 ordinary bootstrap samples.

| Descriptive Test subgroup | Rows / positives | AUC [95% CI] | FNR [95% CI] |
|---|---|---|---|
| Age ≤25 | 1,839 / 214 | 0.738 [0.705, 0.772] | 0.187 [0.136, 0.239] |
| Pensioner | 8,210 / 447 | 0.742 [0.717, 0.764] | 0.539 [0.496, 0.584] |
| Age >65 | 1,163 / 42 | 0.752 [0.676, 0.817] | 0.667 [0.532, 0.810] |
| No bureau history | 6,548 / 667 | 0.755 [0.735, 0.775] | 0.240 [0.210, 0.272] |

Young applicants have weaker observed discrimination and FPR 0.512 [0.488, 0.534]; pensioners/older applicants have higher observed FNR. Groups overlap, small-positive intervals are broad and comparisons are descriptive without multiplicity adjustment. These observations establish neither causality nor legal fairness and do not change the frozen threshold. Full Brier, calibration, approval, FPR/FNR intervals and all subgroup rows are in the evaluation artifact.

Historical contract 1.0: lock `e98453beb10d833e`, recorded model run `cf726679c64d467f8ed86c9837fa57d6`, 46,127 Test rows, one recorded opening. Reported ROC-AUC 0.77320, PR-AUC 0.25114, Brier 0.06735 and log loss 0.24260. That legacy report lacks a separate evaluation run, saved predictions, confidence intervals and subgroup tables. It remains immutable; missing evidence is not retroactively invented. Any authorized new evaluation is disclosed as reuse and must not feed further model choices.

## Population and failure conditions

Only approved Home Credit loans are observed, creating selection/reject-inference bias. `TARGET` means payment difficulty, not default or loss. Relative time fields do not support reliable out-of-time validation; partitions are stratified holdouts with fixed shares/seed. Random-split results do not establish performance on future applicants, other lenders, rejected applications or changed economies.

External scores provide substantial signal and cannot be independently inspected here. Missing or renamed upstream fields, changed source coverage, category shifts, covariate/prior/concept drift and new populations can degrade results. Empty history and unavailable history have different semantics; required unavailable sources are rejected. Explanations describe model contributions, not causal effects.

Monitoring uses a Train-fitted immutable aggregate profile and Selection score/performance reference. Control calibration run `03b14e0ed97c43a48aaaa067ca7eba19` measured 0/100 held-out alerts at the frozen scale after 1/100 calibration alerts. Static batches can overlap; this does not guarantee a production false-alarm rate. Rollback addresses a model regression; shared data/concept drift can affect both versions. Labels are immediate in this simulation, unlike production delay.

The bundle exposes lock/run/artifact/runtime identity, frozen assumptions and parity evidence. Synthetic deployment tests verify exact registered predictions, reload failure safety and rollback validation. Latest real deployment/reproduction/load evidence is tracked separately in [Results](RESULTS.md) and [Operations](OPERATIONS.md). Production gaps include authentication beyond an admin key, encryption/governance, coordinated multi-worker deployment, live monitoring/on-call and independent validation.

## Operational verification

Fresh reproduction run `c3a42f2254144abf9d470f3495e13345` regenerates identical feature values and the identical artifact hash. Actual HTTP verification run `6fa54cc59a9043ba85f8982149f46fc3` checks 500 seeded Train payloads against the registered artifact with maximum absolute probability difference 0, including the frozen threshold/recommendation and an explanation check. Operational registration run `409c012818844b38a980658db22f12b3` established the proof as version 4. Final registration run `28cfaf15adec482d98b12516a4744f2f` serves the identical artifact as version 6 under evaluated lock `402b9e050159762d`. Later changes expose cached aggregate evaluation metadata and update documentation; predictive protocol, artifact and lock remain unchanged.

The controlled rollback exercise completed in run `1f82bddd7d374a7181a57445f65c9bc9`, including gate blocking, audited force promotion, detection, API restoration and the both-version concept-shift case. Mild covariate shift was missed at the declared thresholds; current monitoring evidence and subgroup uncertainty are reported in [Operations](OPERATIONS.md). Pensioner/older control subgroups have weaker observed discrimination/recall with broad intervals (control run `33a3e767bd284070929c04106f841cd6`), not a causal or fairness conclusion.

The load matrix has no request errors, but the indicative p95 target remains unmet. The single-client full-history p95 is 131.0 ms (run `41cc517ba01145b38cf4893d372abb1b`); under concurrency eight it is 859.3 ms with one worker and 351.3 ms with four (runs `666d3ba6f62541efa8892ddf68aa64d8` and `2e384c5b561f4186ada07a39bac466ed`). These are local measurements, not production SLOs. The API returns to one worker for coherent process-local reload.
