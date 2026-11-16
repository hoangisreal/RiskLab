# Business API v1 — đặc tả B00

**Status: đã triển khai local (B01–B05).** Health, auth, Admin user/audit, Analyst
lookup, import/application/review/decision/report routes đều có endpoint và
test trên PostgreSQL thật + Playwright; OpenAPI sinh từ DTO thực tế. Namespace
độc lập scoring [contract 1.1](api_contract.md). Đây chưa phải bằng chứng deploy VPS.
Chạy local theo [application guide](development/application.md).

Nguồn chuẩn: [RBAC](product/rbac.md), [workflow](product/workflow.md),
[import contract](demo_import_contract.md). Mọi quyền dưới đây áp dụng ở server,
không chỉ ở Next.js. Public demo chỉ dùng dữ liệu synthetic đã duyệt.

## 1. Quy ước chung

- JSON UTF-8, snake_case, timestamps RFC 3339 UTC (`...Z`), UUID identifiers
  do server tạo. Unknown fields/query parameters bị từ chối, không mass assignment.
- JSON body tối đa 65,536 bytes; multipart import có giới hạn riêng. Số nguyên
  không nhận bool/string/fraction; floating numbers phải finite. Không nhận NaN/Infinity.
- Success object: `{"data": {...}}`; list: `{"data": [...], "pagination": {...}}`;
  204 không có body. Header `X-Request-ID` do server tạo cho cả success và error.
- `expected_revision`: integer ≥ 1. Application mutation kiểm tra revision của
  application; account mutation kiểm tra revision của account; import retry kiểm
  tra revision của import. Không dùng review sequence thay application revision.
- `reason`: trim, 1–2,000 ký tự; `notes`: tối đa 10,000 ký tự. Lý do/notes không
  được ghi nguyên văn vào operational logs. Business history giữ nội dung theo quyền.
- Generic collection queries: `page` 1–10,000 (default 1), `page_size` 1–100
  (default 20). Sort cố định `created_at DESC, id DESC`; count tính **sau** auth filter.
  Page vượt dữ liệu trả list rỗng, không 404. Offset pagination đủ cho MVP nhỏ;
  dữ liệu thay đổi giữa các trang có thể làm dịch trang, không hứa snapshot pagination.
- Response pagination: `page`, `page_size`, `total` (integer ≥ 0). Không nhận arbitrary
  SQL sort/filter expression hoặc URL/path tải artifact.

### Errors

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Hồ sơ đã thay đổi; hãy tải lại trước khi tiếp tục.",
    "request_id": "00000000-0000-4000-8000-000000000090",
    "details": []
  }
}
```

| HTTP | Codes chính | Quy tắc |
|---|---|---|
| 401 | `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS` | Không phân biệt username tồn tại/disabled qua login error |
| 403 | `FORBIDDEN`, `CSRF_FAILED`, `SEPARATION_OF_DUTIES` | Role/action không được phép |
| 404 | `NOT_FOUND` | Resource không tồn tại **hoặc** ngoài object scope |
| 409 | `REVISION_CONFLICT`, `INVALID_STATE`, `IDEMPOTENCY_CONFLICT`, `LAST_ADMIN`, `RESOURCE_CONFLICT` | Không mutation nghiệp vụ nửa chừng |
| 413 | `PAYLOAD_TOO_LARGE` | Reject trước parse đầy đủ khi có thể; tính cả chunked body |
| 422 | `VALIDATION_ERROR`, `UNSUPPORTED_CONTRACT` | Details chỉ `field` và `code`, không echo input |
| 429 | `RATE_LIMITED`, `IMPORT_CAPACITY_EXCEEDED` | Có `Retry-After`, không giữ job tạo dở |
| 503 | `DEPENDENCY_UNAVAILABLE`, `MODEL_UNAVAILABLE` | Không giả score hoặc success |
| 500 | `INTERNAL_ERROR` | Message an toàn, stack trace không đưa cho client |

Không biến mọi lỗi DB thành 409: phân biệt known constraint conflict với internal
failure. Authentication/object access kiểm tra trước khi lộ resource state.

## 2. Authentication và account API

Cookie `__Host-risklab_session`: Secure, HttpOnly, Path=/, SameSite=Lax, không Domain;
server-side opaque session, absolute lifetime 8 giờ. Local HTTP development chỉ
dùng cookie development riêng; không hạ Secure ở production. Logout/revoke ở DB.

CSRF bootstrap `GET /auth/csrf` cấp token gắn với session hoặc bootstrap context;
JSON `{data: {csrf_token: string}}`, `Cache-Control: no-store`. Mutations (kể cả
login/logout) gửi `X-CSRF-Token`, kiểm tra trusted Origin. Login xoay session/token,
không tiếp tục dùng pre-auth session. Token không nằm trong localStorage/logs/URL.
Implementation B02 dùng cơ chế CSRF/library phù hợp, không tự thiết kế crypto.

| Method/path (sau `/api/v1`) | Actor | Request → response |
|---|---|---|
| GET `/auth/csrf` | Browser, chưa cần login | 200 token; không thông tin tài khoản |
| POST `/auth/login` | Browser | `{username,password}` → 200 `{user,csrf_token}`, Set-Cookie |
| GET `/auth/me` | Authenticated | 200 `User` |
| POST `/auth/logout` | Authenticated | Không JSON body → 204, revoke và clear cookie |
| GET `/admin/users` | Admin | Pagination + `q`, `role`, `is_active` → list `User` |
| POST `/admin/users` | Admin | `{username,password,role}` → 201 `User` |
| PATCH `/admin/users/{user_id}` | Admin | `{expected_revision,reason,role? ,is_active?}` → 200 `User` |
| POST `/admin/users/{user_id}/reset-password` | Admin | `{expected_revision,reason,new_password}` → 204 |
| GET `/admin/audit-events` | Admin | Pagination, optional `user_id` → list account `AuditEvent` |
| GET `/analysts` | Manager | Pagination → active `{id,username}` list để assign |

`username`: lowercase ASCII `[a-z0-9_.-]{3,64}`, unique; password/new_password
12–128 ký tự, không trim password. PATCH yêu cầu ít nhất một field role/is_active;
không rename user hoặc xóa lịch sử trong MVP. Role enum: `ANALYST`, `MANAGER`,
`ADMIN`. Role change/disable/reset password revoke sessions trong transaction.
Self-role-change và mất Admin active cuối bị chặn kể cả concurrent requests.

`User`: `id: UUID`, `username: string`, `role: enum`, `is_active: bool`,
`revision: integer`, `created_at: UTC`. Không trả password hash/session identifiers.
Admin create dùng password truyền qua HTTPS, không trả password lại; provisioning
qua kênh riêng là trách nhiệm operator, không xây email service.

Ví dụ login (giá trị minh họa, không phải tài khoản/password được deploy):

```json
{"username":"analyst.demo","password":"synthetic-example-only"}
```

```json
{"data":{"user":{"id":"00000000-0000-4000-8000-000000000001","username":"analyst.demo","role":"ANALYST","is_active":true,"revision":1,"created_at":"2026-11-10T00:00:00Z"},"csrf_token":"example-token-not-for-use"}}
```

## 3. Import và hồ sơ

| Method/path | Actor | Request → response |
|---|---|---|
| GET `/demo-catalogs` | Manager | List `{version,feature_contract_version,snapshot_count}` của catalog được duyệt |
| POST `/imports` | Manager | Multipart theo [import contract](demo_import_contract.md), Idempotency-Key → 202 `ImportJob` |
| GET `/imports` | Manager | Pagination, optional `state` → list `ImportJob` |
| GET `/imports/{import_id}` | Manager | 200 `ImportJob`, gồm errors an toàn khi FAILED |
| POST `/imports/{import_id}/retry` | Manager | `{expected_revision,reason}` → 202 `ImportJob`; chỉ failed retryable |
| GET `/applications` | Analyst/Manager | Scoped query bên dưới → list `ApplicationSummary` |
| GET `/applications/{application_id}` | Analyst/Manager | 200 `ApplicationDetail` |
| GET `/applications/{application_id}/snapshot` | Analyst/Manager | 200 raw scoring payload từ snapshot, sau object auth |
| GET `/applications/{application_id}/prediction` | Analyst/Manager | 200 `Prediction` đã lưu; 404 nếu chưa có hoặc ngoài scope |
| POST `/applications/{application_id}/scoring/retry` | Manager | `{expected_revision,reason}` → 202 `{job_id,application_revision}`; chỉ FAILED retryable, cùng operation/model |
| PUT `/applications/{application_id}/assignment` | Manager | `{expected_revision,assignee_id,reason}` → 200 `ApplicationDetail` |
| GET `/applications/{application_id}/history` | Analyst/Manager | Pagination → list business `AuditEvent` theo object scope |

`GET /applications` filters: `q` (external_reference substring, 1–64 ký tự ASCII
safe reference), `review_state`, `scoring_state`, `assignee_id` (UUID). Analyst chỉ
được filter chính mình hoặc bỏ field; yêu cầu assignee khác trả 403 không xác nhận
người đó tồn tại. Manager có thể lọc bất kỳ assignee; kết quả không có thì list rỗng.

`ApplicationSummary`:

| Field | Type/meaning |
|---|---|
| `id`, `import_id` | UUID |
| `external_reference` | Safe display reference, không phải applicant ID Kaggle |
| `assignee` | `{id,username}` hoặc null |
| `revision` | Integer ≥ 1, OCC của mọi mutation application/review/decision |
| `feature_availability` | `INCOMPLETE`, `INVALID`, `READY` |
| `scoring_state` | `NOT_REQUESTED`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` |
| `review_state` | `UNASSIGNED`, `ASSIGNED`, `IN_REVIEW`, `PENDING_DECISION`, `DECIDED` |
| `prediction_id` | UUID hoặc null |
| `created_at` | UTC timestamp |

`ApplicationDetail` thêm `source_availability` (source → provided/not_provided),
`missing_sources: string[]`, `supersedes_application_id: UUID|null`,
`catalog_version: string`, `snapshot_id: string`, `input_sha256: string`,
`input_revision: integer` (MVP = 1, khác OCC `revision`),
`current_review: Review|null`, `decision: Decision|null`. Raw payload chỉ lấy từ
snapshot endpoint có quyền, không trộn vào list/search responses.

`ImportJob`: `id`, `revision`, `state` (QUEUED/RUNNING/SUCCEEDED/FAILED),
`catalog_version`, `import_contract_version`, `created_at`, `attempt` (integer ≥ 1),
`total_rows` (integer|null trước parse), `created_count` (0 trừ SUCCEEDED),
`ready_count`, `incomplete_count`, `retryable: bool`, `errors: ImportError[]`.
`ImportError`: `row: integer|null` (header=1), `field: string|null`, `code: string`;
không echo cell/raw data. Cap errors 100; `errors_truncated: bool` bắt buộc.

Ví dụ multipart request và accepted response:

```http
POST /api/v1/imports
Idempotency-Key: synthetic-import-001
X-CSRF-Token: <token-from-auth-csrf>
Content-Type: multipart/form-data; boundary=example

--example
Content-Disposition: form-data; name="import_contract_version"

demo-import/1
--example
Content-Disposition: form-data; name="catalog_version"

synthetic-b00-v1
--example
Content-Disposition: form-data; name="file"; filename="demo.csv"
Content-Type: text/csv

external_reference,snapshot_id,supersedes_application_id
DEMO-001,demo-history,
--example--
```

```json
{"data":{"id":"00000000-0000-4000-8000-000000000020","revision":1,"state":"QUEUED","catalog_version":"synthetic-b00-v1","import_contract_version":"demo-import/1","created_at":"2026-11-10T00:00:00Z","attempt":1,"total_rows":null,"created_count":0,"ready_count":0,"incomplete_count":0,"retryable":false,"errors":[],"errors_truncated":false}}
```

Ví dụ application response sau scoring, trước assign:

```json
{"data":{"id":"00000000-0000-4000-8000-000000000010","import_id":"00000000-0000-4000-8000-000000000020","external_reference":"DEMO-001","assignee":null,"revision":3,"feature_availability":"READY","scoring_state":"SUCCEEDED","review_state":"UNASSIGNED","prediction_id":"00000000-0000-4000-8000-000000000030","created_at":"2026-11-10T00:00:00Z","source_availability":{"bureau":"provided","previous_applications":"provided","installments":"provided"},"missing_sources":[],"supersedes_application_id":null,"catalog_version":"synthetic-b00-v1","snapshot_id":"demo-history","input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","input_revision":1,"current_review":null,"decision":null}}
```

## 4. Review và quyết định

| Method/path | Actor | Request → response |
|---|---|---|
| POST `/applications/{id}/reviews` | Assigned Analyst | `{expected_revision}` → 201 `{application_revision,review}`; chỉ ASSIGNED và score hợp lệ |
| PATCH `/applications/{id}/reviews/{review_id}` | Assigned Analyst | `{expected_revision,notes,proposal}` → 200 `{application_revision,review}`; draft IN_REVIEW |
| POST `/applications/{id}/reviews/{review_id}/submit` | Assigned Analyst | `{expected_revision}` → 200 `{application_revision,review}` |
| POST `/applications/{id}/return` | Manager | `{expected_revision,review_id,reason}` → 200 `ApplicationDetail` |
| POST `/applications/{id}/decisions` | Manager | `{expected_revision,review_id,outcome,reason}`, Idempotency-Key → 201 `{application_revision,decision}` |

`Review`: `id`, `application_id`, `author_id`, `review_number` (integer ≥ 1),
`prediction_id`, `notes`, `proposal: APPROVE|DECLINE|null`, `submitted_at: UTC|null`.
Draft cho phép notes rỗng/proposal null; submit yêu cầu notes trim không rỗng và
proposal có giá trị. Author không được client chọn. Prediction pin khi start;
return tạo draft revision mới cho cùng assignee nếu người đó còn active Analyst,
giữ immutable bản submit cũ. Reassign bỏ current submission/draft pointer, không
xóa history; Analyst mới phải start và model không được tự rescore.

`Decision`: `id`, `application_id`, `review_id`, `prediction_id`, `decided_by`,
`outcome: APPROVED_SIMULATED|DECLINED_SIMULATED`, `reason`, `created_at`.
Server lấy prediction từ review, không nhận arbitrary prediction ID/actor/state.
Manager đã từng review bị cấm; Admin không có quyền; DECIDED không edit/reopen.

Ví dụ save draft, submit và quyết định (revision tăng do server trả về, không dùng
số ví dụ như giá trị cố định trong client):

```json
{"expected_revision":5,"notes":"Đã kiểm tra dữ liệu và giới hạn mô hình.","proposal":"DECLINE"}
```

```json
{"expected_revision":6}
```

```json
{"expected_revision":7,"review_id":"00000000-0000-4000-8000-000000000040","outcome":"DECLINED_SIMULATED","reason":"Đã xem đề xuất Analyst; đây chỉ là quyết định mô phỏng."}
```

Response sau submit (application revision tăng từ 6 lên 7):

```json
{"data":{"application_revision":7,"review":{"id":"00000000-0000-4000-8000-000000000040","application_id":"00000000-0000-4000-8000-000000000010","author_id":"00000000-0000-4000-8000-000000000001","review_number":1,"prediction_id":"00000000-0000-4000-8000-000000000030","notes":"Đã kiểm tra dữ liệu và giới hạn mô hình.","proposal":"DECLINE","submitted_at":"2026-11-10T00:10:00Z"}}}
```

Response sau decision (201; application revision tăng từ 7 lên 8):

```json
{"data":{"application_revision":8,"decision":{"id":"00000000-0000-4000-8000-000000000050","application_id":"00000000-0000-4000-8000-000000000010","review_id":"00000000-0000-4000-8000-000000000040","prediction_id":"00000000-0000-4000-8000-000000000030","decided_by":"00000000-0000-4000-8000-000000000002","outcome":"DECLINED_SIMULATED","reason":"Đã xem đề xuất Analyst; đây chỉ là quyết định mô phỏng.","created_at":"2026-11-10T00:15:00Z"}}}
```

## 5. Prediction, history và báo cáo

`Prediction` gồm `id`, `application_id`, `input_revision`, `input_sha256`,
`feature_schema_hash`, `model_version`, `model_sha256`, `model_run_id`, `lock_id`,
`feature_contract_version`, `protocol_hashes`, `probability`, `threshold`,
`policy_version`, `sources_used`, `warnings`, `explanation`, `created_at`,
`initiated_by`, `job_id`. Hash SHA256 = 64 lowercase hex, probability/threshold
finite [0,1]; release model/run/lock identity phải có, không lấy placeholder từ client.
`policy_version` là identity immutable của threshold/cost policy thuộc bundle,
không là business approval rule. `explanation` giữ `space`, base/raw log-odds và
top contributions của contract 1.1; không diễn giải chúng thành probability deltas.

`AuditEvent`: `id`, `actor_id` (nullable với system), `actor_type` (USER/WORKER/SYSTEM),
`action`, `resource_id`, `created_at`, `request_id` hoặc `job_id`, `reason` nếu có,
`metadata` allowlisted cho action (state/revision/identity deltas, không snapshot/password).
History sort `created_at DESC,id DESC`; quyền business/account audit tách biệt.

| Method/path | Actor | Response |
|---|---|---|
| GET `/reports/workload` | Analyst/Manager | `{total,by_review_state,by_scoring_state,by_assignee}`; Analyst scoped to self |
| GET `/models/current` | Analyst/Manager | `{model_version,model_sha256,feature_contract_version,model_run_id,lock_id,limitations}`; pinned release, không mutable alias identity cho history |
| GET `/models/comparison` | Analyst/Manager | `{report_version,data_manifest_hash,partition_hash,models,limitations}`; aggregate evidence, không row predictions |

`by_*` là list `{value,count}` với count integer ≥ 0; assignee value UUID|null.
Comparison `models` là list `{family,model_run_id,partition_role,metrics}`; metrics
ROC-AUC/PR-AUC/Brier/calibration summary theo report. Không tính metrics mới khi GET.
Không report đầy đủ hoặc chưa có model: 404/503 phù hợp, không số liệu giả.

## 6. Idempotency và transaction semantics

- `Idempotency-Key` bắt buộc cho POST imports/decisions, ASCII `[A-Za-z0-9_-]{8,128}`.
  Scope `(authenticated actor, operation, resource_id nếu có, key)`.
- Import hash: SHA256 của canonical JSON `{import_contract_version,catalog_version,
  file_sha256}`, trong đó file_sha256 băm exact file bytes. Canonical JSON dùng
  sorted keys, UTF-8, separators không khoảng trắng; không gồm multipart boundary/
  filename. Snapshot input hash cũng dùng canonical JSON của raw payload, không
  băm pandas/Parquet bytes vốn có thể khác dù cùng giá trị. Decision hash: canonical validated JSON
  gồm expected_revision/review_id/outcome/reason sau trim.
- Same key/same payload: sau kiểm tra account/role/object permission hiện tại,
  trả original status + response, kể cả revision hiện tại đã tăng; không check
  stale revision trước lookup khiến retry sau thành công bị lỗi sai.
- Same key/different payload: 409, không mutation. Concurrent same key phải hội tụ
  về cùng result bằng DB uniqueness/transaction; crash-before-commit không để lại
  result record nửa chừng. Decision result + audit + idempotency record atomic.
- Import creation response vẫn là 202 original intent khi replay; dùng GET job để
  xem trạng thái hiện tại. Failed retryable job dùng explicit retry endpoint,
  không tạo import mới chỉ bằng replay key cũ.
- MVP giữ keys/results cùng vòng đời application/import; không TTL âm thầm khiến
  retry lâu ngày tạo duplicate decision. Request ID header là ID của request replay,
  không cần trùng request ban đầu; resource IDs/results giữ nguyên.

## 7. Walkthrough contract theo ba role

1. Admin bootstrap → login → tạo Analyst/Manager; GET applications trả 403.
2. Manager login → chọn catalog → multipart import với key → poll job; malformed
   CSV FAILED không để lại hồ sơ, missing source tạo INCOMPLETE không queue scoring.
3. Manager list → assign active Analyst với revision/reason.
4. Analyst login → list chỉ hồ sơ được giao → snapshot/prediction → start/draft/submit.
   Đổi application ID ngoài scope trả 404; gọi decisions trả 403.
5. Manager xem history → return hoặc decide. Reassign/submit/decide dùng revision;
   hai Manager race chỉ một final decision; retry key cũ không tạo bản thứ hai.
6. Admin đổi role Analyst thành Manager → session cũ revoke; login mới vẫn không
   được chốt hồ sơ chính mình từng review. Model reload không đổi prediction history.

Walkthrough này đã có kiểm chứng thực tế: business tests trên PostgreSQL thật
và browser E2E `apps/web/tests/workflow.spec.ts`; kết quả và giới hạn ghi tại
[PROGRESS](PROGRESS.md). Đặc tả này vẫn là nguồn chuẩn cho hành vi route/DTO.
