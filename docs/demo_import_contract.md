# Demo import contract — `demo-import/1`

**Status: đã triển khai local (B03).** B00 chốt layout/fixtures; importer,
worker và persistence thật nằm ở `business/import_csv.py`, `business/catalog.py`,
`business/imports.py` và `business/worker.py` với test PostgreSQL/Playwright.
API: [business v1](business_api_contract.md). Nguồn mẫu:
[fixtures](../tests/fixtures/business_v1/README.md).

## 1. Lựa chọn KISS: CSV tham chiếu catalog synthetic tin cậy

CSV không chứa raw historical tables lồng nhau hoặc features do người dùng tự tính.
Mỗi dòng tham chiếu một snapshot trong **catalog do operator cung cấp và version hóa**.
Snapshot chứa raw application + applicant-local history theo scoring contract 1.1.
Không nhận upload catalog, arbitrary URL, pickle hoặc filesystem path từ browser.

MVP vẫn hỗ trợ batch CSV, nhưng là batch chọn hồ sơ demo. Thu thập hồ sơ mới ngoài
catalog cần quy trình feature acquisition khác và không thuộc contract này.

Multipart `POST /api/v1/imports`, Manager-only, CSRF + Idempotency-Key:

| Part | Giá trị |
|---|---|
| `import_contract_version` | `demo-import/1` (literal) |
| `catalog_version` | Catalog version đã được duyệt, ví dụ `synthetic-b00-v1` |
| `file` | CSV UTF-8, một file duy nhất, không tin filename để chọn path |

CSV header **đúng thứ tự**, không thiếu/thừa/duplicate:

```csv
external_reference,snapshot_id,supersedes_application_id
DEMO-001,demo-history,
DEMO-002,demo-no-history,
DEMO-003,demo-missing-history,
```

| Column | Ràng buộc | Mapping |
|---|---|---|
| `external_reference` | `[A-Za-z0-9_-]{1,64}`, unique trong file, không trim/normalize âm thầm | Application display/source identity trong import, không là model input |
| `snapshot_id` | `[A-Za-z0-9_-]{1,64}`, tồn tại đúng catalog version | Server lookup immutable snapshot payload |
| `supersedes_application_id` | Rỗng hoặc canonical UUID, hồ sơ tồn tại và actor được quyền | Optional replacement link, không sửa/xóa/chuyển state hồ sơ cũ |

External reference có thể lặp giữa hai imports khác nhau; identity của row là
`(import_id, external_reference)`. Duplicate user intent xử lý bằng idempotency key,
không tự suy ra duplicate intent từ cùng nội dung với key khác. Reference không
phải ID người thật/Kaggle và không được dùng làm path.

## 2. Giới hạn và lỗi

- Dùng CSV comma-separated với quoting theo RFC 4180, LF/CRLF, UTF-8 (có thể BOM).
  Không autodetect delimiter/encoding; reject NUL, invalid UTF-8, malformed quotes,
  sai số cột, blank records, duplicate headers và unexpected columns như TARGET.
- File ≤ 1,048,576 bytes; whole multipart body ≤ 1,114,112 bytes; 1–100 data rows.
  Không compressed/archive upload. Field limits vẫn áp dụng sau parse quoted cell.
- Mỗi Manager tối đa 2 active imports (QUEUED/RUNNING). New intent vượt cap trả 429;
  replay key cũ lookup trước cap để không bị chặn retry cùng intent.
- Đây là bounds thiết kế cho demo review thủ công tối đa 100 hồ sơ/lần; không là
  throughput/SLO đã đo. Bộ fixture 3 dòng bao phủ semantics; B03/B06 phải kiểm tra
  biên 0/100/101 dòng, byte limits/chunked bodies và tài nguyên thực tế trước release.
- Snapshot raw payload vẫn bị giới hạn bởi scoring contract 1.1: 5,000 records/source,
  5,000,000-byte payload. Không sửa `configs/api.yaml` chỉ để phù hợp import mới.
- Snapshot có missing source hợp lệ được import INCOMPLETE; invalid raw data hoặc
  reference quan hệ lỗi làm fail cả import, không biến thành no-history.

Synchronous envelope/auth/version/size lỗi trả HTTP 4xx. Envelope hợp lệ tạo job
202; worker kiểm tra CSV/lookup/semantics. Nếu lỗi: FAILED, created_count=0, bounded
safe error report. Nếu snapshot/model integrity không sẵn sàng thì lỗi dependency
retryable; không cho caller ghi đè model version để bỏ check.

Error codes job: `INVALID_CSV`, `INVALID_HEADER`, `ROW_LIMIT_EXCEEDED`,
`INVALID_REFERENCE`, `DUPLICATE_REFERENCE`, `UNKNOWN_SNAPSHOT`,
`INVALID_REPLACEMENT`, `INVALID_SNAPSHOT`, `CATALOG_UNAVAILABLE`, `MODEL_UNAVAILABLE`.
Validation errors không retryable; dependency failures có retry giới hạn trên cùng
intent. Không sửa file của failed job: file mới phải là intent/key mới.

## 3. Catalog ownership và provenance

Catalog version + snapshot ID ánh xạ tới payload immutable. Registry/manifest của
release phải giữ checksum của catalog và từng payload để storage corruption hoặc
thay nội dung dưới cùng key bị phát hiện. Client chỉ chọn version/ID, không gửi hash
được tin cậy. B00 fixtures là data input cho tests, chưa phải signed/deployed catalog.

Khi enqueue, pin model bundle identity và feature contract theo release; snapshot
phải compatible. API worker/retry không resolve lại mutable champion alias.
Mỗi application/prediction lưu catalog version, snapshot ID/checksum, input revision,
feature contract và model/run/lock identity. Snapshot raw không có TARGET/raw IDs.

| Snapshot field | Dùng ở đâu |
|---|---|
| `application.AMT_*`, `DAYS_*`, các field raw được phép | Shared cleaning/schema application |
| `history.bureau` | Bureau features; request-local `bureau_ref` |
| `history.previous_applications` | Previous features; request-local `prev_ref` |
| `history.installments` | Installment features; `prev_ref` hoặc `loan_ref` theo legacy contract |
| `contract_version` | ML feature contract, độc lập import/business API versions |
| `explain` | MVP worker yêu cầu explanation; không dùng thay human review |

## 4. Semantics fixture và kiểm tra

| Fixture | Expected result |
|---|---|
| `demo-history` | READY, 1 bureau/previous/installment record; late ratio = 0 |
| `demo-no-history` | READY: nguồn explicitly provided, records rỗng; counts=0, rates NULL |
| `demo-missing-history` | INCOMPLETE: bureau not_provided; không chạy inference |
| `invalid-history.json` | INVALID: DAYS_CREDIT > 0; reject schema, không publish vào catalog |
| `import-valid.csv` | SUCCEEDED, 3 hồ sơ, 2 READY/1 INCOMPLETE, chỉ 2 scoring jobs |
| `import-duplicate-reference.csv` | FAILED/DUPLICATE_REFERENCE, 0 hồ sơ |
| `import-unknown-snapshot.csv` | FAILED/UNKNOWN_SNAPSHOT, 0 hồ sơ |
| `import-path-injection.csv` | FAILED/INVALID_REFERENCE, không đọc filesystem |
| `import-extra-column.csv` | FAILED/INVALID_HEADER, không nhận TARGET |

Kiểm thử hiện có:
[test_business_contract_fixtures.py](../tests/integration/test_business_contract_fixtures.py)
validate raw schema và chạy shared feature SQL thực. Importer thật được kiểm
chứng thêm tại `tests/business/test_imports.py` và `tests/business/test_workflow.py`
gồm concurrent same-key, different-payload same-key, rollback tại các biên
side-effect, active-import cap và all-or-nothing persistence.
