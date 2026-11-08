# Project status

Current status of RiskLab. Accepted results and evidence are in
`docs/RESULTS.md`; design decisions are in `docs/DECISIONS.md`; architecture
is in `docs/PROJECT.md`.

- Last updated: 2026-11-08
- All planned work is complete: data validation and partitioning, the
  contract-1.1 experiment protocol on Selection, exact reproduction, API
  serving, monitoring, rollback and load evidence, and the final locked Test
  evaluation (run `50d36f3b142a41039ff4d72967242f9a`, lock
  `17b83143e33bb907`).
- Test partition openings: 3 in total, one per evaluated lock (legacy
  `e98453beb10d833e`; contract-1.1 `402b9e050159762d` and
  `17b83143e33bb907`, both evaluating the identical artifact). The append-only
  `reports/final_eval/ledger.jsonl` preserves both. The contract-1.0 result
  is historical only and does not evaluate the corrected contract.

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| Foundations | Done | Fresh-copy setup and CI pass on synthetic data |
| Model comparison (E1) | Done | LightGBM selected over Logistic Regression |
| Experiment protocol (E2–E4) | Done | Source selection, calibration and threshold frozen on Selection |
| Locked Test evaluation | Done | Metrics, confidence intervals and subgroups published in the model card |
| Serving | Done | Registered bundle, 500-payload API parity check, audited rollback |
| Monitoring | Done | 100 calibration and 100 held-out control batches; known limits disclosed |

## Current frozen artifact

LightGBM, no calibration, threshold `0.08040201`, artifact SHA256
`f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`, served as
registry v7 under lock `17b83143e33bb907`. Details: `docs/RESULTS.md` §4–§5
and `docs/model_card.md`.

## Open items

- None blocking. The policy analysis is complete: `CODE_GENDER` stays
  excluded; including it would require an explicit policy decision
  (`docs/DECISIONS.md` D15).
- Any future Test opening requires explicit authorization and a recorded
  reuse acknowledgment before the read (D18).
- Optional production hardening (hosted deployment, authentication beyond a
  local admin key, managed tracking storage) is out of scope unless a
  concrete need appears.

## Follow-up investigations (2026-11-04)

- Mild-shift sensitivity: probed under D19. Alert scale 0.5 qualifies (run
  `f0dafdc48e374218a9267492c291d786`) and detects the mild shift (run
  `cfbe4b583c9b4ee19fb790f2ac657c66`), but it is **not adopted**: adopting it
  needs a full protocol rerun, a new lock and registration, and would detach
  serving from the Test evidence of lock `402b9e050159762d`.
- Latency: profiled in `docs/OPERATIONS.md` §5. The bottleneck is
  per-request DuckDB SQL planning (about 62 ms of about 79 ms
  `prepare_input`); SQL texts are cached per process. The indicative 100 ms
  concurrency target remains unmet and disclosed; no SLO is claimed.
- Subgroups: the Selection replication in `docs/RESULTS.md` §5 shows the age
  FPR/FNR pattern is a base-rate and global-threshold trade-off, not
  per-band mis-calibration. No threshold change was made; group-specific
  thresholds would be a policy and legal decision requiring a new lock.
