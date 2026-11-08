# RiskLab — Accepted Results and Evidence

Contract-1.1 results from the HTTP MLflow server. All choices were made on
Selection with rules declared in config beforehand and frozen before the
owner-authorized final Test evaluation. No Test result informed any choice.
Train 169,131 applicants; Selection 30,751; partition hash
`b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101`.
Comparisons use 1,000 paired resamples, 95% intervals. Per-metric detail and
curves live in the MLflow runs; `docs/model_card.md` is the artifact card.

## 1. E1 — model family (application features)

Runs: LR `1684a06d5fce458f9644bef682bb3e04`, LightGBM
`5b2a7c2d439745f9920412275920b5cd`, comparison `e59165a490914afd8c59dda56ede2752`.

| Selection metric | LR | LightGBM |
|---|---:|---:|
| ROC-AUC | 0.746391 | 0.755967 |
| PR-AUC | 0.235942 | 0.243239 |
| Brier | 0.068291 | 0.067791 |
| Log loss | 0.248925 | 0.246440 |

LightGBM gain +0.009577, CI [0.005621, 0.013116] — clears declared
`min_gain` 0.005 with positive lower bound (D12). Hyperparameters/rounds from
inner 5-fold Train CV, preprocessing refitted per fold.

## 2. E2 — historical sources

Run `620965285a7d40d8b8a11b97d5b101db`. Fixed LightGBM params; a group is
served only if marginal cumulative ROC-AUC gain ≥ 0.003 and paired CI lower
bound > 0.

| Added group | Cumulative AUC | Marginal gain | 95% CI | Serve |
|---|---:|---:|---|---|
| Application + ratios | 0.757348 | — | — | Yes |
| Bureau | 0.761092 | 0.003744 | [0.001147, 0.006449] | Yes |
| Bureau balance | 0.761983 | 0.000891 | [-0.001619, 0.003046] | No |
| Previous | 0.767689 | 0.005706 | [0.002732, 0.008967] | Yes |
| Installments | 0.774311 | 0.006622 | [0.003468, 0.009847] | Yes |
| POS | 0.775338 | 0.001027 | [-0.001168, 0.003170] | No |
| Credit card | 0.777061 | 0.001723 | [-0.000784, 0.004256] | No |

Served set: application, bureau, previous, installments. Cumulative scores
include earlier non-served groups; they are not the served model's score.
Leave-one-group-out is descriptive only: bureau +0.002110 [-0.000352,
0.004695], bureau balance −0.001093, previous +0.003961 [0.001138, 0.006851],
installments +0.002934 [−0.000028, 0.005935], POS −0.000039, credit card
+0.001723. Several intervals include zero — the set is not claimed uniquely
optimal; the rule bounds source/contract burden over small uncertain gains.

### E1 repeated on the served set (the artifact that ships)

Runs: LR `bb0de338703f49ee9d048a67f4b216d5`, LightGBM
`4e46b91eca76426396e82828fa7b3b1e`, comparison
`53f3b77cf9724cad8450ac6a1cd2586c`. 147 inputs; Train CV chose LR C=0.01 and
283 LightGBM rounds.

| Selection metric | LR | LightGBM | Difference | 95% CI |
|---|---:|---:|---:|---|
| ROC-AUC | 0.759367 | 0.772521 | +0.013154 | [0.009109, 0.017429] |
| PR-AUC | 0.252196 | 0.263679 | +0.011483 | [0.003368, 0.019703] |
| Brier | 0.067439 | 0.066776 | −0.000663 | [-0.001007, -0.000332] |
| Log loss | 0.245145 | 0.241557 | −0.003588 | [-0.004878, -0.002436] |

LightGBM wins the unchanged rule. This fitted artifact is frozen for E3.

## 3. Policy analysis (descriptive, not a selection)

Run `5071cd0fcd5348bb8df5589e925d2a10`, fixed LightGBM on served set.

| Policy | Selection AUC | Δ vs default | 95% CI |
|---|---:|---:|---|
| Exclude gender, keep age (default) | 0.772176 | — | — |
| Include gender | 0.775803 | +0.003627 | [0.001206, 0.006057] |
| Exclude gender + declared age inputs | 0.771646 | −0.000531 | [-0.002819, 0.001801] |

Decision: keep gender excluded, age included (D15). A discrimination gain
does not resolve the policy question; inclusion needs explicit owner
approval. Removing age inputs does not remove all age proxies.

## 4. E3 calibration / E4 threshold

E3 run `e1f58f0e2ad14e74980b7ac5a855191b` on the frozen artifact above.
Calibrators fit Calibration only; comparisons score Selection.

| Method | Log loss | Brier | ECE (15 bins) |
|---|---:|---:|---:|
| None | 0.241557 | 0.066776 | 0.004573 |
| Sigmoid | 0.241619 | 0.066771 | 0.004397 |
| Isotonic | 0.249040 | 0.066894 | 0.003600 |

Chosen: **none** (lowest log loss, simplest). Artifact SHA256
`f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`.

E4 run `82f54a4c01fe4f90829fceeaef60a269` reuses that exact artifact.
Threshold **0.08040201** (Bayes reference 0.09090909). At threshold on
Selection: approval 0.692238, precision 0.178888, recall 0.682111, proxy cost
14,533.40/applicant (FP 0.05×credit, FN 0.50×credit — proxies, TARGET is
payment difficulty, not measured loss). Constant-cost comparison chooses
0.09090909; the 9-point LGD/margin grid spans thresholds 0.040201–0.356784.

## 5. Locked Test evaluation (final, owner-authorized)

Run `50d36f3b142a41039ff4d72967242f9a`, lock `17b83143e33bb907`, clean source
`9237788`, 46,127 rows, unchanged artifact and threshold. Opening 3 total,
1 for this lock (D20). The previous lock `402b9e050159762d` evaluated the
identical artifact SHA256 with identical metrics. Intervals: locked 1,000-resample stratified bootstrap.

| Test metric | Value | 95% CI |
|---|---:|---:|
| ROC-AUC | 0.775432 | [0.767577, 0.782915] |
| PR-AUC | 0.256541 | [0.245630, 0.269374] |
| Brier | 0.067150 | [0.066568, 0.067690] |
| Log loss | 0.241822 | [0.239487, 0.244109] |

At threshold 0.08040201: approval 0.691569, precision 0.179940, recall
0.687433, FPR 0.275146, FNR 0.312567, ECE 0.007156. Subgroups: 23 of 28
levels published (5 suppressed < 200 rows / 20 positives). Descriptive
weaknesses: young applicants (AUC 0.738 [0.705, 0.772], FPR 0.512),
pensioner/older recall (FNR 0.539 / 0.667). No causal or fairness claim; no
decision followed. Full table: `docs/model_card.md`.

Selection replication (descriptive, E3 predictions run
`e1f58f0e2ad14e74980b7ac5a855191b`, same frozen artifact/threshold): the
age pattern is already present where decisions may look. Base rate falls
monotonically with age (≤25: 0.119 → 65+: 0.034) and mean predicted
probability tracks it closely per band (0.121 → 0.036), so the global
threshold mechanically produces FPR 0.521 / FNR 0.176 at ≤25 and FPR
0.071 / FNR 0.692 at 65+ (pensioner FNR 0.521); within-band AUC stays
0.731–0.787. This is a base-rate/threshold trade-off, not per-band
mis-calibration. Any group-specific threshold is a policy/fairness
decision requiring owner + legal review and a new lock (standing policies
in `docs/DECISIONS.md`); none is adopted.

Historical contract-1.0 (immutable, does not evaluate contract 1.1): lock
`e98453beb10d833e`, model run `cf726679c64d467f8ed86c9837fa57d6`, ROC-AUC
0.77320, PR-AUC 0.25114, Brier 0.06735, log loss 0.24260; no CIs/subgroups —
missing evidence is not backfilled.

## 6. Leakage diagnostics (summary)

Runs: application `bde0b31723554136ab4f1157b1f75fc8`, served
`be9a520b3fee4c77aef17c6cbda51aeb`. Declared beforehand: permutation band
±0.02 around 0.5, screening deviation 0.20, max gain share 0.50, max
|Train-CV − Selection AUC| 0.05.

- Permutation AUCs all in band (application 0.495–0.505; served 0.488–0.503);
  overlap power control detected (0.847 / 0.864). No unexplained
  single-feature screen flag.
- Served model: largest gain share 0.15543 (`EXT_SOURCE_3`), Train-CV −
  Selection AUC gap −0.00440 — both under thresholds. Importance is split
  gain, not causal.
- EXT_SOURCE scores cannot be audited upstream; Train univariate AUCs
  0.321/0.335/0.343 (reversed direction counts). Diagnostics complement —
  they do not prove zero leakage. Full controls table: `docs/archive/leakage.md`.

## 7. Completion evidence (Phases 0–4)

| Requirement | Accepted evidence (run ID) |
|---|---|
| Fresh setup, synthetic CI, provenance | 380 non-data tests pass; lint/format/mypy pass; digest-pinned images |
| Partition roles, Train-only transforms | Loader/fit/hash guards; Test refused outside locked eval |
| Historical validity, exact feature semantics | Hand fixtures; all 7 Train feature tables reproduce exactly — reproduction `c3a42f2254144abf9d470f3495e13345`, identical artifact hash |
| E1/E2/E3/E4, policy, diagnostics | Runs in §1–§4, §6; rules declared before running |
| Independent metric check | `scripts/recompute_metrics.py` (plain sklearn) matches accepted runs within 1e-9 |
| Served == selected artifact | HTTP golden `6fa54cc59a9043ba85f8982149f46fc3`: 500 Train payloads, max prob diff 0 |
| Registration | v7 `3d11d2293dcf4ad8b77e558c6cb57a48` under lock `17b83143e33bb907` (same artifact SHA256 as v6) |
| Monitoring controls | `dd00fae6c1ad4f3baec4e1c594f867b8`: scale 1.0, 1/100 calibration alerts, 0/100 held-out |
| Monitoring scenarios | Summary `f1d5df3f91014aaebe595dfaab7ed6b3`: 3 injected bugs + strong age + label shift detected; **mild age shift missed (disclosed)**; Kaggle unlabeled has no performance section |
| Rollback exercise | `1f82bddd7d374a7181a57445f65c9bc9`: gate block → audited force-promote → detection → CLI rollback/API restore |
| Load matrix | `666d3ba6f62541efa8892ddf68aa64d8` (1 worker), `2e384c5b561f4186ada07a39bac466ed` (4 workers): 0/200 errors per case; **indicative 100 ms p95 target unmet (disclosed, no SLO claim)** |
| Final Test | §5, run `50d36f3b142a41039ff4d72967242f9a`, lock `17b83143e33bb907` |

## 8. Known limitations (demo vs production)

| Aspect | RiskLab | Real production |
|---|---|---|
| Data | Static Kaggle snapshot; stratified holdouts (no absolute dates → no reliable out-of-time split) | Continuous data; out-of-time validation |
| Labels | Immediate in simulation | Delayed 6–12+ months |
| Population | Approved loans only (reject-inference bias); `TARGET` = payment difficulty, not default/loss | Reject inference; live experiments |
| Features | Client-sent raw records | Bureau integrations, feature stores |
| Deployment | Alias change + in-process reload, 1 worker | Immutable bundles, shadow/canary |
| Governance | Model card + audit log; subgroup stats are descriptive, not fairness findings | Independent validation, fair-lending review |
| Security | Localhost + admin API key; MLflow unauthenticated localhost | AuthN/AuthZ, encryption, PII law |
| Tracking | SQLite single-writer under `mlflow-data/` | PostgreSQL + object storage |
| Monitoring | Static pool, batches may overlap; mild shift missed at declared thresholds | Live populations, delayed labels |

Selection is reused for a small predeclared decision set, so Selection
metrics are mildly optimistic; the locked Test estimate in §5 is the
unbiased one. Published AUC benchmarks are context only (D7).
