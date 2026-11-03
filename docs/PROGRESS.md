# RiskLab Progress

Short handoff log. The plan lives in `docs/MASTER_PLAN.md`.

- Last updated: 2026-11-03
- Current phase: contract 1.1 accepted through Selection, strict real parity and Monitoring controls; independent reproduction, operational deployment, faults/rollback and load matrix verified; final Test approval pending. The earlier contract-1.0 Test result is historical evidence only: lock e98453beb10d833e, recorded model run cf726679c64d467f8ed86c9837fa57d6. It does not evaluate the corrected contract.
- Test partition openings: 1 (lock e98453beb10d833e; see `reports/final_eval/ledger.jsonl`)

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 0. Foundations | Done | Fresh-copy setup and current branch CI pass on synthetic data |
| 1. MVP | Done | E1 chosen model: LightGBM; later Test evaluation is recorded below |
| 2. Portfolio-ready ML | Contract 1.1 through Selection/parity verified | Accepted E1/E2/policy/diagnostics/E3/E4 below; independent reproduction passed; new Test requires explicit approval |
| 3. Portfolio-ready serving | Real registry/API/rollback verified | Clean-commit bundle registration, real 500-payload golden check and audited rollback passed |
| 4. Advanced | Full-engine controls verified | 100 calibration / 100 held-out controls; real faults/rollback/subgroups/load verified; mild shift missed and latency target unmet, both disclosed |
| 5. Optional | Not planned | Only on concrete need |

## Next actions (Phase 0)

- [x] Project skeleton: `pyproject.toml`, `uv.lock`, ruff, mypy, pytest markers, pre-commit, Makefile, `.gitignore`, `.env.example`
- [x] Library versions pinned in uv.lock; FrozenEstimator, MLflow allowed-hosts and LightGBM deterministic settings checked against current official docs.
- [x] Typer CLI skeleton and pydantic config loading
- [x] `risklab ingest`: CSV → Parquet with explicit types, data manifest (real data: 17s, peak RSS ~2.0 GiB, row counts match)
- [x] Shared run-manifest utility
- [x] Synthetic data generator for all eight raw tables (`src/risklab/synthetic.py`)
- [x] CI workflow on synthetic data; upstream action SHAs pinned and current review branch CI passes
- [x] `docs/data_contract.md` draft (numbers from `scripts/raw_anomalies.py`)

## Phase 1 tasks

- [x] Partition assignment (`risklab partition`): seed 20261101, shares 55/10/10/15/10, stratified on `TARGET`. Real data: train 169,131 / calibration 30,752 / selection 30,751 / test 46,127 / monitoring 30,750 rows, positive rates 0.08071–0.08074. `partition_hash` b26bba642ca01b6240cd5f38f21cd0e546b5f975f3ce99cf847eb8f3f35a9101
- [x] Role-gated loaders (`risklab.data.loaders.load_partition(role, purpose)`): only train/fit, calibration/calibrate, selection/select, monitoring/monitor are readable; Test always raises `TestAccessError`. Every load re-verifies role ids, `partition_hash` and the source Parquet/CSV hashes. AST guard test in `tests/unit/test_loaders_isolation.py`
- [x] Selection lock and `final_eval` (the only code allowed to open Test; MASTER_PLAN 4.3) — implemented in `models/select.py` + `models/final_eval.py` with ledger + reuse acknowledgment; synthetic integration test covers lock, single opening and idempotent re-run. The final real-data lock is e98453beb10d833e; Test was opened once
- [x] Application validation (`risklab validate`, SQL-driven, no Pandera) and cleaning (`clean_application`, declarative `configs/cleaning.yaml`, row-wise). Real data: 0 errors; one accepted known exception (application_test 224393, `REGION_RATING_CLIENT_W_CITY = -1`). Orphan/duplicate handling for historical tables is deferred to Phase 2
- [x] Application training frame (`load_application_frame`: `load_partition` → `clean_application` → drop excluded columns) and unfitted LR / LightGBM pipelines (`risklab.models.pipelines`). Preprocessing statistics are learned only inside `.fit`; leakage tests compare fitted state with independently computed Train statistics. Added deps: pandas 3.1, scikit-learn 1.9.1, lightgbm 4.7. E1 results are recorded below.
- [x] MLflow tracking server (`make mlflow`: SQLite backend, proxied artifacts, 127.0.0.1:5000) and run logging with provenance tags (partition hash, config hash, git commit and dirty flag, library versions) in `risklab.tracking`
- [x] Evaluation module (`risklab.models.evaluate`): ROC-AUC, PR-AUC, Brier, log loss, paired bootstrap, prediction files; independent check `scripts/recompute_metrics.py`
- [x] E1 (`risklab e1`): LightGBM preferred under the pre-declared rule (ROC-AUC +0.00958, 95% interval [+0.00562, +0.01312] on Selection). MLflow runs c9009e8a..., 9316731e..., b3f2b675.... Details in `docs/experiments.md`. Rerun reproduced the results exactly
- [x] Leakage diagnostics (`risklab diagnostics`): permutation AUCs within 0.5 +/- 0.006, positive control detected, no feature beyond the screening threshold (`docs/leakage.md`, run 86bbc743...)
- [x] EDA report on Train only (`scripts/eda_application.py`, written to `reports/eda/`, not committed). Notebook replaced by a script so the report is reproducible and Train-only by construction


## Historical contract-1.0 implementation and runs

- [x] Historical validation (`risklab validate-history`), feature SQL for all six history groups plus application ratios (`sql/features/`), registry `configs/features.yaml`, batch build (`risklab features`) and online parity path sharing the same SQL and cleaning code
- [x] Calibration (`models/calibrate.py`: FrozenEstimator calibrators, ECE, simpler-method rule), threshold/cost (`models/threshold.py`: Bayes check, sensitivity), E2/E3/E4 runners (`risklab e2|e3|e4`, configs declared in advance)
- [x] Registry promotion gate, alias CLI with audit log, FastAPI (`/predict`, `/models/current`, `/health`, `/monitoring/evaluate`, admin reload), Dockerfile + docker-compose
- [x] Monitoring: PSI/KS drift, labeled-batch performance, subgroup report with suppression, seeded batch simulator
- [x] Run E2/E3/E4 on real data with MLflow tracking (runs cf726679…, 7296da8d…, b24c3fff… in `docs/experiments.md`); champion trained (calibration none, threshold 0.0839, served groups previous+installments) and initially locked as 58ddf70a4f14f5b0, superseded by e98453beb10d833e before the single recorded Test opening. Any further opening still requires explicit owner approval and a recorded reuse acknowledgment
- [x] Monitoring control check (0/10 alerts), rollback demo (gate block + force-promote + detection + rollback, audit-logged) and load baseline (`scripts/load_test.py`; Locust not adopted — httpx script covers the report)

## Decisions log

| Date | ID | Decision |
|---|---|---|
| 2026-11-01 | D1 | MLflow UI + Markdown reports in Phases 1–4; Streamlit optional in Phase 5 |
| 2026-11-01 | D2 | Experiment 2 decides which historical tables the API serves; correctness and simplicity first |
| 2026-11-01 | D3 | No fixed hardware or schedule; memory-bounded DuckDB + Parquet pipelines |
| 2026-11-01 | D4 | English for docs, code, comments, commit messages |
| 2026-11-01 | D5 | Test used only for final evaluation of a locked selection |
| 2026-11-01 | D6 | Serve the evaluated artifact; no refit after selection |
| 2026-11-01 | D7 | Published AUC benchmarks are context only |
| 2026-11-01 | D8 | Ingest casts from all-VARCHAR with a regex guard for integer columns, because DuckDB's typed CSV read rounds `2.5` to `3` silently |
| 2026-11-01 | D9 | Partition seed `20261101` and shares 55/10/10/15/10 are fixed. Assignment orders ids by BLAKE2b(`seed:id`) per class, with no RNG stream. Changing either requires `--overwrite` and invalidates all downstream results |
| 2026-11-01 | D10 | Validation is SQL-driven over Parquet in DuckDB, not Pandera/pandas: no new dependency, bounded memory, reusable for historical tables. Rules live in config. Known data exceptions are listed by table, check and id with a reason; stale exceptions warn |
| 2026-11-01 | D11 | Cleaning is declarative and strictly row-wise (sentinel to NULL plus flag, `XNA` to NULL), so it is safe to apply before partitioning and reusable by the API |
| 2026-11-01 | D12 | E1 and the leakage diagnostics are declared in `configs/experiments/e1.yaml` and `diagnostics.yaml` before any run: primary metric ROC-AUC, `min_gain` 0.005, 1,000 paired bootstrap resamples, 95% interval, LightGBM preferred only if gain >= min_gain and the interval lower bound > 0, otherwise Logistic Regression. Permutation band 0.02, screening threshold 0.20 |

## Open items

- Policy analysis is complete (docs/experiments.md): keep CODE_GENDER excluded; inclusion still requires owner approval.
- New gate, serving-validation, verification and final-report settings are explicit YAML configs. Legacy artifacts remain unchanged; contract-1.1 decisions and threshold come from the separately tracked Selection experiments below.

## Handoff notes

- Phase 0 verified in a clean copy without `data/`: `uv sync --locked`, ruff, mypy, 63 tests pass; `data` tests skip.
- Ingest atomicity: all tables are staged and verified before any file is replaced. Remaining narrow window: a crash during the final file swaps or the manifest write can leave the manifest stale; the next partial ingest detects this by hash, and a full ingest repairs it.
- Parquet files are not byte-reproducible (`preserve_insertion_order=false`); raw SHA256 and row counts are the exact references.
- Historical raw-data issues are handled in contract 1.1: orphan attribution is explicit, the 653,483 excess installment-key rows are partial payments, and configured sentinels are cleaned. See data_contract.md.
- MASTER_PLAN 7.1 revised: `MONTHS_BALANCE = 0` (snapshot at application) is allowed.
- Resolved pre-API review items (shared conversion/cleaning now implemented): (1) `CODE_GENDER` is a `category_to_null` cleaning rule column, so `clean_application` requires it even though it is excluded as a feature; the API should not have to collect gender just to clean. (2) The Arrow-to-pandas conversion (float64 casts, None→NaN in object columns, column order) lives inside `load_application_frame`; factor it into a shared `frame_from_table` used by batch and online paths, with a parity test, because `OneHotEncoder` treats `None` and `NaN` as different missing values.
- Historical pre-partition target associations included all of `application_train`, including Test rows. They do not justify the current cleaning/feature policy. EDA and any modelling decision must use `load_partition("train", "fit")`.

## Review corrections (2026-11-03)

- Reload now loads/validates a pinned artifact before swapping the store; failure keeps the current model. Registry loading requires complete, matching bundle metadata and exact runtime/input compatibility.
- Promote re-evaluates Selection metrics against the current champion and records its gate report. Force requires an audit reason and does not bypass bundle validation. Rollback requires an audited prior promotion.
- Locks bind config/SQL hashes and complete runtime versions; final evaluation verifies the current data/partition hashes and clean tree before any read. An exclusive file lock and durable pre-read ledger entry prevent concurrent/crashed evaluations from silently reopening Test.
- E3 persists the selected fitted artifact and Selection predictions. E4 and candidate preparation/registration reuse it without refitting. The legacy champion can be validated/repackaged against its stored Selection metrics without Test reuse.
- Parity now compares all model columns and probabilities through the actual API input builder. Real-data parity passed on 500 seeded Train applicants; no Test rows were read.
- Monitoring evaluates registered simulation specs for the requested pinned version. Missing columns, missing-rate changes and score PSI participate in alerts. Serving bundles now persist an aggregate reference profile, fitted on Train in a Pipeline, with the Selection score reference. The loader verifies its bytes against the pinned version before swapping the model.
- Verification fails closed for absent/malformed references and compares raw hashes, stable counts, partition hashes and Selection aggregates. Seven aggregate reference files are included from the recorded runs. Optional local feature baselines support per-value comparison. `make reproduce OUT=...` isolates derived outputs and never opens Test or changes aliases.
- Supplemental leave-one-group-out analysis is implemented for future E2 runs without changing the cumulative decision rule. New final-evaluation reports include metric CIs, subgroup intervals, prediction files and their own MLflow run id. The legacy Test report/ledger remain unchanged; missing legacy CIs/subgroups cannot be backfilled without separately authorized reuse.
- Threshold decision: missing-rate alert = 0.20, declared in monitoring.yaml before new simulations; existing PSI/KS and promotion tolerances retained. Old 0/10 control results describe score PSI only and do not establish the false-alarm rate of the complete corrected engine.
- Verification: regression tests and complete synthetic registry/serving checks are run as part of this change; see the final check record below. GitHub CI and a fresh real-data compose deployment still need operational verification after commit.

### Earlier evidence gaps

Served-set E1/leave-one-out/history diagnostics, full-engine controls, real strict-bundle deployment, rollback and load checks were pending at the earlier correction snapshot. They are now verified in the accepted completion evidence below. New Test evidence remains approval-dependent.

### Earlier correction check record (2026-11-03)

- `make test`: ruff, format and mypy passed; 318 non-data tests passed. After adding the last lock/isolation guards and forwarding reporting settings, the focused regression suite passed all 34 tests (including the nine additional non-data cases).
- `pytest tests/data/test_real_parity.py -m data`: passed on the final Arrow-based request extraction, checking all feature values and probabilities for 500 seeded Train applicants.
- `scripts/recompute_metrics.py reports/experiments/e1`: all eight recorded metrics reproduced within 1e-9; original run ids are printed by the script.
- `risklab verify`: all seven aggregate references passed against the stored reports. `docker compose config --quiet`, `git diff --check`, final lint/format and mypy passed.
- Full real-data isolated reproduction and a fresh compose registration were not executed. Synthetic HTTP registry integration covers the deployment chain and bit-identical model predictions. The legacy champion SHA256 and the single-entry Test ledger remain unchanged; no live alias was moved.

## Completion goal continuation (2026-11-03)

- Completion audit: docs/COMPLETION_AUDIT.md now enumerates mandatory Phase 0–4 evidence gaps. The earlier correction turn made verified progress; completion is not yet proven.
- D13 (before new experiments): feature contract 1.1 corrects installment partial-payment aggregation, two-level loan aggregates and NULL no-history rates; application ratios are part of the engineered application baseline. These are data-contract corrections, not choices based on Test results. Existing 1.0 artifacts/Test evidence remain immutable.
- Historical cleaning now has explicit configured event cutoffs/missing-event handling and sentinel/category rules. Existing partition shares/seed, E1/E2 gain rules, calibration comparison rules and constant cost assumptions remain unchanged. Future experiments must log the corrected contract and cannot claim the legacy Test result for a new artifact.

- D14 (before contract-1.1 experiments): E1 is repeated on E2's served set and its chosen fitted artifact is frozen for E3; E3 no longer refits the chosen family. Per-loan proxy costs use LGD 0.50 and margin 0.05 times AMT_CREDIT; E4 chooses the global threshold on Selection under this declared policy and reports the original constant-cost comparison plus a fixed LGD/margin grid. These are illustrative assumptions, not measured losses; no Test result informs this choice.

- Contract-1.1 cost grids include both probability endpoints 0 and 1. Cost and monitoring reports use the same frozen per-loan assumptions exposed by the serving bundle.

- Shared contract-1.1 SQL fixture checks, typed API limits/logging checks, exact bootstrap comparisons against scikit-learn, explanation conservation, immutable profile validation and the synthetic HTTP registry chain have passed focused checks. The complete updated suite is being rerun; earlier final-check counts above describe the earlier correction snapshot.

- D15 (before contract-1.1 experiments/monitoring): the policy analysis is a descriptive, fixed-LightGBM gender/age ablation with 1,000 paired resamples; default exclusion of gender and retention of age remain unchanged. Served-set diagnostics additionally flag an importance share above 0.50 or an absolute Train-CV/Selection AUC gap above 0.05.
- Monitoring declares 100 calibration and 100 held-out control batches with disjoint seed ranges. The smallest of predeclared alert scales [1, 1.25, 1.5, 2] meeting the 5% calibration target is frozen per model/profile/config before held-out measurement. Performance alerts use ordinary-bootstrap 99% intervals (500 resamples) and configured relative tolerances/absolute floors. Control batches share a static pool and may overlap; no model choice uses them.
- Complete non-data suite passed: 357 tests at the pre-reporting snapshot. The subsequent focused Selection/monitoring/provenance suite passed 35 tests; new controls and public-payload changes still require their own checks.

- CI actions are pinned to official upstream commits, resolved and verified through GitHub on 2026-11-03. Existing GitHub CI is green for the unchanged base commit d8a6291; it is not evidence for this worktree. Development/CI uses available Python 3.12.14 and uv 0.12.21; the digest-pinned train/API image uses Python 3.12.16. Registered artifacts must be prepared/locked in the train image. A host attempt to install 3.12.16 was unavailable in uv's download catalog, so the supported host version remains 3.12.14.

- D16: the first contract-1.1 candidate was rejected because unordered floating aggregates changed 9 Selection predictions across processes despite an unchanged artifact (diagnosis in reports/operations/candidate-metric-diagnosis.log). No lock, registration, alias or Test opening occurred. SUM/AVG inputs now have an explicit value order in the shared SQL; no values are rounded and no decision tolerance is relaxed. The initial contract-1.1 experiments are superseded and retained locally before rerunning the unchanged decision rules.

### Accepted contract-1.1 evidence

- E2 run `4e467506bab74355b13bca284dacbb87` includes all six leave-one-out probes and selects application+bureau+previous+installments. Repeated E1 comparison `2b1c9ef2f9f94984bb0900c54ec96887` chooses LightGBM; model run `cbcfa9885a7e4caa982885b8adbb5cc4`. All rules/budgets remained unchanged after the ordered-aggregate correction.
- Policy run `ca1631574f494d1c85c0fbb0e4c97a2f` keeps the declared gender exclusion/age retention. Application and served leakage diagnostics pass in runs `51eb3b0ef61747ba93d7582512f88d7e` and `d7bfa962dc1b44adb0766becfd85a1ab`.
- E3 run `9a84a8cc279846e88b110f06ee3ea69c` chooses none around the frozen served E1 artifact; E4 run `92aa4caf17fd434f95ad13dd80de5198` selects threshold 0.08040201 under the declared per-loan assumptions. The artifact SHA256 is `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`.
- Candidate preparation reproduced all E3 Selection metrics exactly under the strict 1e-9 tolerance, and public-schema feature/probability parity passed on 500 seeded Train applicants. Historical SQL hand fixtures additionally test row-order/thread stability; Test remains untouched.
- Full-engine controls run `03b14e0ed97c43a48aaaa067ca7eba19`: scale 1.0, 1/100 calibration alerts and 0/100 held-out alerts, below the configured target. The policy is bound to model/profile/config hashes. The batches share a static pool and can overlap.
- Latest complete synthetic suite: 369 passed, 9 data tests deselected; lint/format/mypy and diff checks pass. One earlier scanner failure misidentified the unlabeled Kaggle filename as the Test role file; the exact-name distinction now has a regression test. Starlette emits one upstream testclient deprecation warning; tests still pass using the existing dependency set.
- Nine corrected aggregate reference files were exported separately under `reports/reference/contract-1.1/`. The earlier references remain untouched. The independent, disk-backed image reproduction under `reports/workspaces/reproduction-contract-1.1` passed, including per-feature comparison against the ordered Train baseline (run below).

- D17 (before the behavioral probe): test the frozen model's mean response on a seeded Train sample by changing each normalized EXT_SOURCE input from 0.2 to 0.8 while holding other inputs fixed. The expected direction is lower mean risk; this is a descriptive diagnostic, not a model-selection or monotonicity constraint. No refit or new feature decision follows it.


### Operational completion evidence

- Independent reproduction run `c3a42f2254144abf9d470f3495e13345` passed all nine aggregate references and exact value/missing-mask equality for all seven Train feature tables. The fresh artifact SHA256 is identical; fresh E3 run `4322585f9c1140c789cb0950bec06101`. No Test or alias change occurs in reproduction.
- All accepted E1/E2/E1-served/E3/policy metrics recompute with plain scikit-learn within 1e-9; each originating run has its independent check artifact. Frozen-model Train-only directional tests pass without refitting.
- Clean source commit `1e6624c` produced operational lock `f7107d92f1159055` and version 4 (registration run `409c012818844b38a980658db22f12b3`). Actual HTTP golden run `6fa54cc59a9043ba85f8982149f46fc3` checked 500 Train payloads, max absolute probability difference 0, threshold/recommendation identity and an explanation. Runtime metadata/schema/profile checks pass.
- Scenario-summary run `f1d5df3f91014aaebe595dfaab7ed6b3` detects all three upstream bugs, strong age shift and label-only shift; mild age shift is missed and disclosed. The real Kaggle batch is unlabeled and returns no performance section. Monitoring.md records source runs and subgroup CIs/weaknesses.
- One-command rollback run `1f82bddd7d374a7181a57445f65c9bc9` blocks diagnostic v5, force-promotes with an audited reason, detects the regression, restores v4 via CLI/API reload and shows both models degrade under shared concept shift. Diagnostic cards carry their own artifact/metric identity. The legacy model and one-entry Test journal hashes are unchanged.
- Load runs `666d3ba6f62541efa8892ddf68aa64d8` (one worker) and `2e384c5b561f4186ada07a39bac466ed` (four workers) have 0/200 errors in each empty/full-history case at concurrency eight. Single-client diagnostic `41cc517ba01145b38cf4893d372abb1b` also has no errors. The indicative 100 ms target is unmet; measured figures and hardware are published without a production SLO claim. Serving returns to one worker.
- Final `make test`: lint/format/mypy pass; 373 synthetic tests pass, ten data tests deselected, one upstream Starlette testclient deprecation warning. This includes failure-preserving reload guards for unknown source groups and label/identifier inputs. The final train/API images build successfully. Current private review and revision checks: https://github.com/hoangisreal/RiskLab/pull/1. Final deployment identity and CI links are retained locally in `reports/operations/final-deployment.json` and attached to the registration run, avoiding a documentation commit that would invalidate its clean-revision lock.
- Remaining owner decision: one explicitly authorized, disclosed Test reuse for a final clean-revision lock of the already frozen artifact. No model/feature/calibration/threshold decision remains to be made. Optional Phase 5 infrastructure remains out of scope.
