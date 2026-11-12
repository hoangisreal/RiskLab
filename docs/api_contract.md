# API contract

## Scope: existing scoring API

This is the implemented scoring contract, not the planned authenticated business
API. The admin key on model reload is **not** account authentication, RBAC or a
Manager approval permission. The current public response semantics remain unchanged.

Contract **1.1** uses raw application fields and applicant-local history. The current E2 decision consumes `bureau`, `previous_applications` and `installments`. `/models/current` is authoritative for a pinned deployment's groups, columns, cost assumptions, Selection run and lock. Contract-1.0 registry entries cannot load in this API image.

| Endpoint | Behavior |
|---|---|
| `POST /predict` | Shared cleaning and feature SQL, calibrated probability, frozen threshold, `approve`/`review`, model/contract version, source groups, warnings and optional explanation |
| `GET /models/current` | Pinned model identity, registration time, purpose, artifact/profile hashes, feature columns/schema hash, protocol/runtime provenance, calibration, frozen costs, Selection metrics and matching completed Test evidence |
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

`final_evaluation` contains cached aggregate Test metrics, confidence intervals, row count, evaluation run/lock IDs and total Test openings for the exact loaded bundle. It is `null` without a complete matching lock/report/journal record, including legacy reports without their own evaluation run. Reading this metadata never evaluates Test or reads row predictions. `registered_at_ms` is the registry creation timestamp in Unix milliseconds. `feature_schema_hash` hashes the contract version, ordered input columns and served groups; `protocol_hashes` separately exposes config/SQL/implementation hashes. Test results are reporting evidence and never participate in the Selection promotion gate.

## Ranh giới API nghiệp vụ MVP — triển khai một phần

- Contracts cụ thể ở [business API v1](business_api_contract.md) và
  [demo import](demo_import_contract.md), kèm fixtures synthetic được kiểm tra với
  raw schema/shared SQL. B01/B02 đã có auth/account/health endpoints; import/review/
  decision chưa triển khai. Xem [application guide](development/application.md).
- API nghiệp vụ tương lai có namespace version riêng (dự kiến `/api/v1`), account
  authentication, object authorization, state/revision guards và audit theo
  [RBAC](product/rbac.md), [workflow](product/workflow.md). Chưa công bố endpoint
  hoặc schema chưa có implementation như một contract đang hoạt động.
- Model recommendation `approve/review` là tín hiệu minh họa, không phải đề xuất
  Analyst hoặc human decision. Không đổi semantics/rename field của contract 1.1
  âm thầm; consumer cũ cần compatibility tests và migration note khi có thay đổi.
- Chỉ Manager chốt quyết định mô phỏng; business API phải tham chiếu persisted
  prediction và immutable review, không gọi `/predict` rồi tự tạo approval.
- UI đọc prediction metadata đã lưu, không dùng `/models/current` để suy ra model
  từng dùng cho mọi hồ sơ. Model reload không sửa lịch sử prediction/decision.
- Trước public deployment, legacy predict/monitoring/admin routes phải được giữ
  private hoặc bảo vệ bằng authorization thích hợp; không tạo đường vòng qua
  Caddy hoặc direct port để bỏ RBAC. Admin account không mặc nhiên có quyền reload model.
- API namespace version và ML feature `contract_version` là hai version độc lập.
  Thay HTTP auth/route không được bỏ serving bundle integrity hoặc parity checks.
