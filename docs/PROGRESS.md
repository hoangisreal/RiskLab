# RiskLab Progress

Short handoff log. The plan lives in `docs/MASTER_PLAN.md`.

- Last updated: 2026-11-01
- Current phase: Phase 0 complete (first local commit); Phase 1 next
- Test partition openings: 0 (see `reports/final_eval/ledger.jsonl` once it exists)

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 0. Foundations | Done | Fresh-copy check passed without real data; CI not yet run on GitHub |
| 1. MVP | Not started | |
| 2. Portfolio-ready ML | Not started | |
| 3. Portfolio-ready serving | Not started | |
| 4. Advanced | Not started | |
| 5. Optional | Not planned | Only on concrete need |

## Next actions (Phase 0)

- [x] Project skeleton: `pyproject.toml`, `uv.lock`, ruff, mypy, pytest markers, pre-commit, Makefile, `.gitignore`, `.env.example`
- [ ] Pin library versions and recheck against current docs (sklearn `FrozenEstimator`, MLflow allowed-hosts, LightGBM 4.x). Deferred: these libraries are added in Phase 1–3.
- [x] Typer CLI skeleton and pydantic config loading
- [x] `risklab ingest`: CSV → Parquet with explicit types, data manifest (real data: 17s, peak RSS ~2.0 GiB, row counts match)
- [x] Shared run-manifest utility
- [x] Synthetic data generator for all eight raw tables (`src/risklab/synthetic.py`)
- [x] CI workflow on synthetic data (not yet run on GitHub; action pins are major tags, SHA pinning pending)
- [x] `docs/data_contract.md` draft (numbers from `scripts/raw_anomalies.py`)

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

## Open items

- Confirm default policy to exclude `CODE_GENDER` from model features (decide in Phase 2 before the selection lock).
- Set `min_gain`, ECE binning, gate tolerances, and cost assumptions in config before the related experiments.

## Handoff notes

- Phase 0 verified in a clean copy without `data/`: `uv sync --locked`, ruff, mypy, 63 tests pass; `data` tests skip.
- Ingest atomicity: all tables are staged and verified before any file is replaced. Remaining narrow window: a crash during the final file swaps or the manifest write can leave the manifest stale; the next partial ingest detects this by hash, and a full ingest repairs it.
- Parquet files are not byte-reproducible (`preserve_insertion_order=false`); raw SHA256 and row counts are the exact references.
- Raw data issues to resolve in Phase 1 validation (see `docs/data_contract.md`): orphan `SK_ID_BUREAU` / `SK_ID_PREV` rows, 653,483 duplicate installment keys, `365243` sentinels.
- MASTER_PLAN 7.1 revised: `MONTHS_BALANCE = 0` (snapshot at application) is allowed.
