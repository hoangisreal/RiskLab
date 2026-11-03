# API contract

Contract **1.1** uses raw application fields and applicant-local history. The current E2 decision consumes `bureau`, `previous_applications` and `installments`. `/models/current` is authoritative for a pinned deployment's groups, columns, cost assumptions, Selection run and lock. Contract-1.0 registry entries cannot load in this API image.

| Endpoint | Behavior |
|---|---|
| `POST /predict` | Shared cleaning and feature SQL, calibrated probability, frozen threshold, `approve`/`review`, model/contract version, source groups, warnings and optional explanation |
| `GET /models/current` | Pinned model identity, purpose, artifact hash, provenance, feature groups, calibration and frozen cost assumptions |
| `GET /health` | Independent liveness and model readiness |
| `POST /monitoring/evaluate` | Registered `batch_id` and optional pinned `model_version`; unknown IDs return 404 |
| `POST /admin/models/reload` | Admin key from the environment; validate/download before swapping; failure keeps the loaded model |

A minimal no-history request for the selected source set is:

```json
{
  "contract_version": "1.1",
  "application": {"AMT_INCOME_TOTAL": 200000, "AMT_CREDIT": 500000},
  "history": {
    "bureau": {"status": "provided", "records": []},
    "previous_applications": {"status": "provided", "records": []},
    "installments": {"status": "provided", "records": []}
  },
  "explain": true
}
```

`provided` with empty records asserts no history: count features are zero and other historical aggregates are NULL. `not_provided` or an absent required source returns 422. Structurally valid unused sources are ignored with a warning.

The OpenAPI field schema is generated from `raw_schema.yaml`, the application ranges and historical contract. Income and credit are required; other application fields are nullable. Raw applicant/loan IDs, `TARGET`, `CODE_GENDER`, computed ratios and cleaning flags are forbidden. Numbers must be finite JSON numbers; booleans, numeric strings and fractional integer fields are rejected. Unknown keys return 422. The configured numeric ranges apply; unknown categorical strings are accepted and produce warnings according to Train-fitted category sets.

Bureau and balance records use a request-local `bureau_ref`. Previous applications use unique `prev_ref` values. Installment/POS/card records use exactly one `prev_ref` pointing to a provided parent or a standalone `loan_ref` when no parent record is available. Standalone identities must be distinct from parent identities. Parent references must be unique; duplicate loan-month records, identical payment records and conflicting installment due dates are rejected. An installment needs its version/number and realized/due dates. Separate partial payments of the same installment remain valid and are summed before comparison with its due amount. These relational rules are checked after field validation.

Realized events must be non-future and required event dates cannot be missing. Planned future dates remain valid where the data contract permits them. Historical sentinels/categories use the same row-wise cleaning as batch SQL. Floating SUM/AVG inputs have explicit ordering, so a different record layout cannot silently change a tree branch.

`configs/api.yaml` caps each source at 5,000 records and the entire body at 5,000,000 bytes, including chunked bodies. Too-large bodies return 413; record/type/reference violations return 422. Validation errors omit echoed input values. Logs contain request ID, pinned version, status and latency, never payloads or admin keys. A request retains one model snapshot throughout validation and prediction.

Explanations contain the base model's raw log odds, base value and largest original-feature contributions. LR one-hot and missing-indicator terms are grouped back to original inputs; LightGBM uses native TreeSHAP. The contribution sum reconstructs the base log odds. Sigmoid/isotonic calibration is a later mapping, so those terms do not decompose calibrated probability. They are diagnostic model explanations, not regulated adverse-action reasons.

Bundles require `serving_config.json`, an immutable aggregate `reference_profile.json`, a model card, compatible runtime/input columns, full parity evidence and a content-bound selection lock. The profile's exact bytes, feature list, model hash, contract and Selection aggregates are checked before loading. Reload failure returns 502 and preserves the prior model. A diagnostic rollback artifact is identified by `purpose=rollback_demo_only`.
