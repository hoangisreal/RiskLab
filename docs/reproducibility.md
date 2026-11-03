# Reproducibility

Seeds, budgets, decision rules and costs are declared before experiments. All predictive preprocessing is fitted inside Train pipelines, including inner-CV transforms. E1 on the served set persists both fitted models; E3 calibrates its selected frozen winner and saves the exact chosen artifact. E4, candidate preparation and registration reuse it. No refit follows selection.

Shared historical SQL orders floating SUM/AVG inputs explicitly. This matters for tree models: a tiny unordered summation difference can cross a learned split. Contract-1.1's initial unordered run was rejected by candidate verification, retained under `reports/legacy/contract-1.1-unordered`, and superseded. No tolerance was relaxed to admit it. Source/data/protocol hashes record the distinction.

New locks bind the complete model/runtime, data manifest, verified partitions, config/SQL and predictive implementation hashes. Candidate verification additionally checks that E2/E3/E4 refer to the current protocol, that E4 used the same artifact, that Selection metrics reproduce, and that all model features/probabilities agree on seeded public-schema Train payloads. The current clean git commit is required before locking or registration.

## References and isolated reproduction

`reports/reference/*.json` preserves the earlier contract-1.0 aggregate baseline. `reports/reference/contract-1.1/` is the separately declared corrected baseline. Both contain hashes, stable counts and Selection aggregates with source run IDs, never applicant rows, predictions, models or Test results. A failed historical comparison is not overwritten merely to pass verification.

```bash
uv run risklab verify --reference reports/reference/contract-1.1
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
make reproduce OUT=reports/workspaces/reproduction
```

For the exact training/serving interpreter and image, run the isolated reproduction inside the train service:

```bash
docker compose run --rm --entrypoint /app/.venv/bin/python train \
  scripts/reproduce.py --out /workspace/reports/workspaces/reproduction-image \
  --feature-reference /workspace/features
```

The output root must be absent or empty. Use a disk-backed location with room for Parquet/features/spill; `/tmp` may be too small. The runner copies configs/SQL, redirects all derived data/reports and preserves raw inputs, partition shares and seeds. It runs ingest/validation/features, E1/E2, repeated served E1, policy/diagnostics, E3/E4 and candidate verification. It never opens Test, changes existing partition files or moves aliases. The isolated output is not a deployment checkout; create locks in the reviewed repository.

Verification fails for absent/empty/malformed references, missing outputs, changed raw/partition hashes or metrics outside the configured tolerance. Parquet bytes can vary across layouts; their hashes identify concrete files but are not a cross-run equivalence criterion. `--feature-reference DIR` compares every local baseline feature by applicant key, column and missing-value mask with relative tolerance 1e-9. Aggregate JSON alone cannot prove per-row equality. Same-image metric tolerance is 1e-6; cross-platform tolerance up to 1e-3 is contextual, not automatically applied or guaranteed.

`snapshot-reference --out DIR` exports an explicitly chosen aggregate baseline. Record protocol changes and investigate mismatches before using it. Full manifests describe inputs, outputs, config, git, versions and platform; experiments also log available input manifests and their own stage manifest to MLflow.

## Independent checks

`scripts/recompute_metrics.py reports/experiments/EXPERIMENT` imports no RiskLab code and checks Selection ROC-AUC, PR-AUC, Brier and log loss with plain scikit-learn. It supports E1, repeated E1, E2 cumulative/leave-one-out, E3 and policy predictions. It requires the recorded source run ID. Prediction files remain ignored by git.

Future authorized final evaluations save their own run, locked report, CIs/subgroups, predictions and manifest. They remain separate from reproduction. The legacy Test report has no saved prediction file; missing historical uncertainty is not silently backfilled.

## Runtime and upstream verification

Development/CI is pinned to Python 3.12.14 and uv 0.12.21. The digest-pinned train/API image uses Python 3.12.16. Host experiments are usable for exploration; registered artifacts are prepared/locked in the train image so its exact runtime matches serving. Both images resolve the same `uv.lock`. Library drift or predictive source/config drift prevents a load.

The implementation was checked against the official [scikit-learn calibration API](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV), [MLflow server security documentation](https://mlflow.org/docs/latest/self-hosting/security/network/) and [LightGBM parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html) on 2026-11-03. Calibration uses `FrozenEstimator` with disjoint calibration rows; compose explicitly permits the `mlflow:5000` Host header; LightGBM uses configured deterministic CPU settings. CI actions are pinned to official upstream commit IDs.

## Accepted fresh reproduction

Run `c3a42f2254144abf9d470f3495e13345` records the completed isolated image reproduction. All nine aggregate references pass, all seven Train feature tables are exactly equal by key/value/missing mask, and the fresh chosen artifact has the same SHA256 `f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`. Fresh E3 run `4322585f9c1140c789cb0950bec06101` reproduces baseline E3 `9a84a8cc279846e88b110f06ee3ea69c`; fresh 500-applicant parity also passes. The reproduction summary/log are attached to the dedicated run. No Test or alias operation occurs in the runner.

Independent scikit-learn checks also pass for every accepted baseline E1/E2/E1-served/E3/policy metric; each originating run has its aggregate recomputation log under `independent_checks/`. The tracked source IDs are in experiments.md. The existing legacy model and Test-journal SHA256 checks remain unchanged.
