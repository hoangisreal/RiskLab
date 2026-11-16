# Portfolio verification — 2026-11-16

Objective: reproduce the baseline, investigate bounded improvements and retain
enough evidence to assess ML engineering work. Do not automatically replace
the serving model.

## Machine and CUDA

Verified machine: Fedora 45, i5-12500H (16 logical CPUs), approximately 15 GiB
RAM, RTX 3050 Laptop with 4,096 MiB, compute capability 8.6, driver 620.70.06.
The driver was already functional; no reinstall or system reconfiguration was
needed. Missing sandbox device nodes can make `nvidia-smi` fail; these results
were confirmed outside the sandbox.

Executed `uv run python scripts/gpu_preflight.py --out reports/operations/gpu-preflight-20261116.json`:
XGBoost and CatBoost both fitted synthetic data on GPU without fallback warnings.
XGBoost's effective device was also checked in its booster configuration. Fit
times were approximately 0.423 and 0.277 seconds; this is a small smoke test,
not a benchmark or Home Credit evaluation. Wheels from the existing `uv.lock`
environment worked without installing an additional CUDA toolkit.

E6 runs one trial at a time, with maximum depth 6 and four CPU threads.
[XGBoost](https://xgboost.readthedocs.io/en/stable/gpu/index.html) documents memory
use as dependent on data and histograms.
[CatBoost](https://catboost.ai/docs/en/features/training-on-gpu) documents
non-deterministic GPU training; GPU artifacts are not required to match CPU
bytes. Baseline LightGBM remains on CPU for comparison with historical evidence.

## Reproduction

Run from the root with `uv sync --locked` and Python 3.12. Place licensed raw
Home Credit data in `data/raw`, outside Git. Choose an output that does not exist:

```bash
uv run python scripts/portfolio_run.py --out reports/workspaces/fresh-baseline
uv run python scripts/portfolio_run.py --out reports/workspaces/fresh-e6 \
  --baseline reports/workspaces/fresh-baseline/baseline
```

The runner limits each execution to 12 hours, starts an isolated loopback MLflow
HTTP server with its own database/artifacts/logs, and stops it on completion or
failure. It does not use the historical tracking store or alter the Docker demo.
Baseline execution covers ingest, partitioning, validation, features,
E1/E2/E1-served, policy, diagnostics, E3/E4, candidate and reference verification.
Seeds/shares, SQL, preprocessing, features and metric tolerances are unchanged.
No locks are created, models registered or aliases changed; dirty source state
is recorded accurately in provenance.

E6 was declared in `experiments/e6.yaml`: four XGBoost configurations × three
Train folds, with preprocessing learned separately and early stopping on each
fold's validation data. Configuration/rounds are selected by Train-CV before
Selection scoring. XGBoost uses native categorical processing with category sets
learned on Train, unlike E5's one-hot preprocessing; differences cannot be
attributed solely to tuning. Comparisons use LightGBM with 283 rounds on the
same served set, removing each historical source, each `EXT_SOURCE_*` and the
whole group. Fault simulation sets external scores to missing without changing
the API's required-source validation.

ROC-AUC is primary; a research flag requires gain ≥ 0.005 and a positive lower
paired 95% confidence bound (1,000 bootstrap resamples). The flag does not
authorize promotion. Selection has been reused; intervals are exploratory and
uncorrected for multiple comparisons. Threshold scenarios describe recall/review
workload; the serving policy is unchanged. A real Test opening requires separate
authorization for the specific lock.

## Completed execution

Baseline at `reports/workspaces/portfolio-20261116` completed: 9/9 aggregate
reference checks passed, candidate parity passed on 500 cases, and Test remained
unopened. Seven Train feature tables (169,131 cases) matched the local baseline
exactly in values, keys and missing masks, rtol=atol=0. Aggregate report:
`reports/workspaces/portfolio-20261116/feature-comparison.json`.
E3 run `0f127989b0d3416ea4072e10a986f980`, E4 `ea551cfe090546e78c93733a7ef58e8c`;
the E3 artifact SHA256 exactly matches the published [model card](../model_card.md).
LightGBM Selection ROC-AUC 0.772521, PR-AUC 0.263679, Brier 0.066776, log loss
0.241557; threshold 0.08040201. No new lock or serving-model replacement.

## E6 results (Selection, not a new Test evaluation)

Run `c6fb772e904046599de3263ebfee27e7`, 169,131 Train / 30,751 Selection rows.
Config SHA256 `98bb53e8db5b00dd4e2bdaff8a5959ac98ba6aaff5346c4ff6aece73f5dea948`;
source SHA256 `8fa0deedbbbe666324547ae21426c53cbb1387c0b8a87f1b8d580a04b615f26a`.
Local report: `reports/workspaces/portfolio-e6-20261116/study/summary.json`.

| Model | ROC-AUC | PR-AUC | Brier | Log loss |
|---|---:|---:|---:|---:|
| LightGBM reference | 0.772521 | 0.263679 | 0.066776 | 0.241557 |
| XGBoost CUDA, selected by Train-CV | 0.774047 | 0.266446 | 0.066641 | 0.241023 |

Train-CV selected depth 4, min_child_weight 30, 621 rounds and learning rate 0.03.
AUC gain +0.001527; paired 95% CI [-0.000693; +0.003593]. This fails the rule
of +0.005 with a positive lower bound, so no superiority flag was assigned and
LightGBM remains champion. Search was not expanded after examining Selection.
Fit/evaluation took 167.3 seconds, excluding data loading/tracking startup;
this is not a CPU/GPU speed benchmark.

Research artifact SHA256
`cc982140538f4c88a7671e539c04ae433390cfb5437b26438802d8cc7f2e0d32`.
Training device was verified as `cuda:0`; prediction uses CPU. Pipeline
serialization/reload preserved probabilities exactly, maximum difference 0.
A separate Python process reloaded the artifact and directly recomputed four
sklearn metrics on Selection: maximum metric difference 0, with matching
source/artifact hashes. Aggregate check: `study/independent-verification.json`.
The challenger is not integrated with the application's explanation/registry
contract and cannot automatically replace the champion.

| Ablation: refit LightGBM on remaining inputs | ROC-AUC | Δ AUC vs reference |
|---|---:|---:|
| Remove all EXT_SOURCE inputs | 0.745761 | -0.026759 |
| Remove EXT_SOURCE_1 | 0.769770 | -0.002751 |
| Remove EXT_SOURCE_2 | 0.759478 | -0.013042 |
| Remove EXT_SOURCE_3 | 0.764151 | -0.008369 |
| Remove bureau from served set | 0.769158 | -0.003363 |
| Remove previous from served set | 0.767110 | -0.005411 |
| Remove installments from served set | 0.765407 | -0.007114 |

These are conditional contributions on the serving sources, unlike cumulative
E2. Do not add gains or interpret them causally. Removing each historical source
reduced AUC in this measurement. The EXT_SOURCE_1 interval includes 0;
comparisons are exploratory, without multiple-testing correction.

Keeping the champion but simulating all three EXT_SOURCE inputs missing at
inference yielded AUC 0.728064, PR-AUC 0.205051, Brier 0.069755 and log loss
0.256085. This differs from refitting without external scores and is not an
accepted fallback model. The result supports prioritizing upstream-score
availability/quality. Never fill required sources with empty data to bypass
API validation.

At the frozen threshold, Selection review fraction is 30.78% and recall 68.21%.
A scenario threshold of 0.05 gives review fraction 48.69% and recall 83.32%:
a descriptive trade-off, not a new policy or group-specific threshold.

New results remain separate from [historical RESULTS](../RESULTS.md).

## Additional benchmarks and current evidence

- [E7](model-comparison.md): 12 models, run `05a1da3d6f3c4900ab0ebe12a119cdcf`.
  XGBoost AUC 0.774287; gain +0.001766 fails the superiority rule. The champion
  is unchanged. Independent reload of all 12 artifacts: maximum metric difference 0.
- [E8](temporal-validation.md): three models on official training data, run
  `5ce5550e61724c478e3e3fb0a003ad3d`. 300,000 Train, 33,400 Selection rows,
  11 predictors; LR AUC 0.615878. Independent reload of three artifacts:
  maximum metric difference 0. Calibration/Test/Monitoring labels and competition
  Test remain unopened.
- Dummy was removed from current code/reports at the owner's request; previous
  runs remain intact locally. Seeds, budgets and hyperparameters were not changed
  in response to results.
- [Evidence JSON](benchmark-evidence.json) contains metrics, paired/block
  intervals, config/source/artifact hashes and reload status, without personal
  data or per-row predictions. It is an aggregate report; raw artifacts remain private.

## Current local gate

| Command/check | Result on 2026-11-16 |
|---|---|
| `make test` | 742 passed, 10 deselected; 242.54 seconds; real disposable PostgreSQL |
| Ruff / format / mypy within `make test` | Pass; 219 files formatted, 91 source files typed |
| `uv run python scripts/check_public_files.py --links` | Pass; 360 candidate paths |
| `git diff --check` | Pass |
| Frozen protocol and three historical evidence files | Unchanged hashes/bytes |
| GitHub Markdown + Mermaid parsing/rendering | Pass; images and diagram visually inspected |

The initial browser CI at snapshot `82eb52f` exposed a test race: reloading and
counting rows while the table was still loading. Local reproduction confirmed
that the worker had persisted three cases (two SUCCEEDED), while repeated DOM
probes observed loading state. The test now polls worker status through the API,
then uses auto-wait assertions for three UI links, without mocking scoring,
increasing timeouts or adding retries. After the fix,
`npm run test:e2e:workflow`: **4 passed (39.2 seconds)** on desktop/mobile;
`npm run lint`, `npm run typecheck` and `npm run format:check` all passed.

There is one Starlette TestClient deprecation warning; dependencies were not
changed to hide it. Pytest checks use synthetic data only. The frontend
production build was rebuilt with Docker for screenshots; full frontend/E2E
gates run in CI, whose run status is linked below.

## Application and acceptance limits

The README includes three real screenshots of a local production build with
real API/PostgreSQL/worker, using synthetic cases/model.
[Image provenance](../assets/README.md). Mermaid parsing/rendering was checked;
Markdown was rendered through GitHub's API. `make demo-up` is a trial workflow,
not production deployment.

CI covers Python, real PostgreSQL, frontend build, Docker scoring smoke and
Playwright desktop/mobile. Individual run status:
[GitHub Actions](https://github.com/hoangisreal/RiskLab/actions/workflows/ci.yml).
VPS/TLS, an image-registry release and off-VPS recovery are not accepted.
Passing CI does not establish production readiness.

## Public scope and source identity

Public candidate files, relative Markdown links and whitespace are checked with
`scripts/check_public_files.py --links` and `git diff --check`. CI runs the same
policy. The local pre-push guard checks every outgoing commit, including private
files added and deleted before the tip; new refs trigger a full ancestry check.
Local hooks do not accompany clones; the CLI and CI are included in the repository.

Cleanup/history mapping and audit limitations: [source revisions](source-revisions.md).
Local guides, original Git history and backup bundles are retained. Frozen
protocol, locks, references, metric tolerances and the original bytes of
RESULTS/model card/leakage remain unchanged. No lock/promotion or real Test
opening was performed for this work.
