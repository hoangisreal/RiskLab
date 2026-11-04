# RiskLab Progress

Short handoff log. Context: `docs/PROJECT.md`. Decisions: `docs/DECISIONS.md`.
Accepted results/evidence: `docs/RESULTS.md`. Full history through
2026-11-04: `docs/archive/PROGRESS-2026-11-04.md`.

- Last updated: 2026-11-04
- Current phase: mandatory Phases 0–4 verified, including contract-1.1
  Selection, exact reproduction, real API/monitoring/rollback/load evidence,
  and the owner-authorized final Test report (run
  `a9fc07a3cbaf43ab88d450ed6a3eca58`, lock `402b9e050159762d`).
- Test partition openings: 2 total, one per evaluated lock (legacy
  `e98453beb10d833e`; contract-1.1 reuse `402b9e050159762d`). The append-only
  `reports/final_eval/ledger.jsonl` preserves both. The contract-1.0 result
  is historical only and does not evaluate the corrected contract.

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 0. Foundations | Done | Fresh-copy setup and branch CI pass on synthetic data |
| 1. MVP | Done | E1 chose LightGBM |
| 2. Portfolio-ready ML | Done | Experiments, independent reproduction, locked Test metrics/CIs/subgroups, model card verified |
| 3. Portfolio-ready serving | Done | Clean-commit bundle registration, real 500-payload golden check, audited rollback passed |
| 4. Advanced | Done | 100/100 monitoring controls; real faults/rollback/subgroups/load verified; mild shift missed and latency target unmet, both disclosed |
| 5. Optional | Not planned | Only on concrete need |

## Current frozen artifact

LightGBM, calibration none, threshold `0.08040201`, artifact SHA256
`f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`, served as
registry v6 under lock `402b9e050159762d`. Details: `docs/RESULTS.md` §4–§5,
`docs/model_card.md`.

## Open items

- None blocking. Policy analysis is complete: keep `CODE_GENDER` excluded;
  inclusion requires explicit owner approval (`docs/DECISIONS.md` D15).
- Any future Test opening requires a new explicit owner authorization and a
  recorded reuse acknowledgment before the read (D18).
- Phase 5 infrastructure is out of scope unless a concrete need appears.

## Handoff notes for the next agent

- If this file disagrees with the code, the code wins; update this file.
- Do not cite a metric without its MLflow run ID (plus lock ID for Test).
- Record new decisions in `docs/DECISIONS.md`, not here. Keep entries short.
- Ask before: deleting data/`mlflow-data/`, changing partition seed/shares,
  moving registry aliases outside the documented CLI, or opening Test.
