# Monitoring

Batches are simulated from the Monitoring pool (seeded, registered by `batch_id` in the monitoring manifest; the API never accepts file paths) plus the unlabeled Kaggle `application_test` drift sample. Scenarios: control, covariate shift toward younger applicants, upstream bugs (income ×1000, 60% missing `EXT_SOURCE_3`, renamed category), prior/concept shift via label flips.

Drift is measured against the model version's reference profile: per-feature PSI and KS effect size, score PSI (reference = Selection scores), and missing-rate change. P-values are not reported. Alert thresholds in `configs/monitoring.yaml` must be calibrated so at most 5% of control batches alert. Labeled batches also report ROC-AUC, PR-AUC, Brier, ECE, calibration-in-the-large, and expected cost at the served threshold. Subgroup gaps (gender, age bands, income type, education, region, contract type, bureau-history) are descriptive with bootstrap CIs; small groups are suppressed.

Rollback scenarios: (a) a corrupted-data candidate is blocked by the promotion gate; (b) a force-promoted bad version is detected on a labeled batch and rolled back with `risklab registry rollback`; (c) drift degrading both versions shows rollback does not fix data drift. Load testing (Locust, application-only vs full-history payloads) should report p50/p95/p99, throughput, and error rate before claiming any latency target.

## Verified results (2026-11-03)

- Rollback demo: corrupted candidate v3 (`EXT_SOURCE_*` set constant in training, Selection ROC-AUC 0.72792 vs champion 0.76929) was **blocked by the promotion gate**; after a recorded force-promote, Monitoring-pool ROC-AUC was 0.72809 vs 0.76955 for the champion, and `registry rollback` restored v1 (`reports/registry_audit.jsonl`, `reports/monitoring/rollback_demo.json`). Note: an income ×1000 corruption (v2) was *not* blocked — LightGBM is scale-invariant per feature, so that fault class needs the drift checks, not the gate.
- Load check (sequential httpx client, one API worker, no-history payload, compose stack): p50 248 ms, p95 322 ms, p99 325 ms, 3.9 req/s, 0/40 errors (`scripts/load_test.py`). This misses the indicative p95 < 100 ms target; the per-request DuckDB setup dominates. Treat as a baseline, not a tuned result.
- Control batches: 0/10 alerts at score PSI 0.25 (see `docs/experiments.md`).
