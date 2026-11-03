# API contract

Base contract version `1.0` (bumps whenever the served feature set changes).

## Endpoints

| Endpoint | Behaviour |
|---|---|
| `POST /predict` | Versioned request; runs cleaning + the shared feature SQL in in-memory DuckDB, then the calibrated model. Returns probability, threshold, `approve`/`review`, model/contract version, `sources_used`, warnings, optional explanation. |
| `GET /models/current` | Champion version, run/lock IDs, threshold, contract version, feature groups. |
| `GET /health` | Liveness plus readiness (model loaded and validated). |
| `POST /monitoring/evaluate` | Accepts a registered `batch_id` only (never a file path). |
| `POST /admin/models/reload` | API key from `RISKLAB_ADMIN_API_KEY`; failed reload keeps the old model serving. |

## History source states

Each history source carries `status`: `provided` with records aggregates with the training SQL; `provided` with `records: []` asserts no records (record count 0, other aggregates NULL); `not_provided` or an absent key for a required source is rejected with 422 naming the missing sources — missing history is never imputed as no history. Unused sources are ignored with a warning. Dangling `bureau_ref`/`prev_ref` references return 422. Request payloads are never logged.
