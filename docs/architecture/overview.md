# Kiến trúc MVP mục tiêu

**Triển khai local (B01–B06 tooling):** Next.js shell/login/Admin + workflow UI,
business FastAPI, PostgreSQL migrations đến `0004_workflow`, sessions/RBAC,
local storage, PostgreSQL job queue/worker và production Compose/Caddy/CI/
backup tooling. Chạy local theo [application guide](../development/application.md).
Deploy VPS thật vẫn là mục tiêu B07. Code ML/API cũ tại [PROJECT.md](../PROJECT.md).
Phạm vi theo [PRD](../product/prd.md); ưu tiên KISS/YAGNI và modular monolith.

## 1. Deployment view

```text
Browser ── HTTPS ── Caddy
                     ├── /       → Next.js
                     └── /api/*  → FastAPI ─── PostgreSQL
                                      │            ▲
                                      ▼            │
                                Local storage ← Python worker

Training CLI (ngoài VPS phục vụ) → MLflow → pinned model bundle → release
```

- Next.js là frontend TypeScript, không có database/business backend thứ hai.
- FastAPI và worker dùng cùng Python codebase/image, khác entrypoint.
- PostgreSQL lưu nghiệp vụ/session/jobs, DuckDB/Parquet xử lý analytical features.
- Worker không phải microservice độc lập. Không Redis/Celery, message broker,
  Kubernetes, Terraform hoặc S3 implementation trong MVP.
- MLflow tiếp tục phục vụ experiment/registry nội bộ; không public và không bắt
  buộc luôn chạy trên VPS web. Model bundle phục vụ phải pin và kiểm tra integrity.

## 2. Ranh giới code

| Khu vực mục tiêu | Trách nhiệm |
|---|---|
| `apps/web/` | Next.js UI, routing, presentation và API client |
| `src/risklab/api/` | HTTP validation, auth dependencies, routes, error mapping |
| `src/risklab/business/` | Business API/identity, PostgreSQL, audit, storage; không mount legacy API |
| Các module nghiệp vụ trong `src/risklab/` | Hồ sơ, import, scoring orchestration, reviews, identity, audit |
| `src/risklab/workers/` | Claim/retry jobs và gọi application services |
| Infrastructure adapters | PostgreSQL sessions, storage, configuration |
| ML packages hiện có | Data/feature/training/inference/registry/monitoring |
| `migrations/` | Alembic migrations của DB nghiệp vụ |
| `deploy/` | Compose/Caddy/deployment config tương lai |

Đây là định hướng ownership, không yêu cầu tạo trước mọi package rỗng hoặc chuyển
toàn bộ code ML sang cây mới. Giữ `src/risklab` và các entrypoint đang dùng đến
khi có migration được kiểm thử. Chỉ tách module khi có trách nhiệm cụ thể.

Route/worker/CLI gọi cùng service, không nhân bản business logic. ML không được
phụ thuộc HTTP/ORM nghiệp vụ; refactor dependency ngược hiện có phải bảo vệ parity.
Không tạo generic repository/framework chỉ để một storage adapter dùng được.

## 3. Authentication và UI

Theo [RBAC](../product/rbac.md), FastAPI sở hữu local accounts và server-side
sessions. Next.js không tự phát token/quyết định quyền và không truy cập PostgreSQL.

- Caddy phục vụ một origin cho web và `/api/*`, tránh cấu hình cross-origin dư thừa.
- Không cache dùng chung trang/API có dữ liệu người dùng. SSR gọi API phải chuyển
  tiếp session của đúng request, không dùng credential đặc quyền dùng chung.
- Nếu dùng Server Actions/route handlers, mỗi mutation vẫn phải được FastAPI
  xác thực/authorize; không dựa vào middleware hoặc layout guards của Next.js.
- Loading/empty/error/forbidden states phải có thông báo rõ; score không được
  trình bày như phán quyết tự động. Chốt decision cần confirmation và lý do.
- Pin phiên bản Next.js/React khi tạo app; không giả định API của phiên bản chưa chọn.

## 4. Persistence và jobs

Nhóm bảng tối thiểu: `users`, `sessions`, `applications`, `import_jobs`, `jobs`,
`predictions`, `reviews`, `decisions`, `audit_events`. Thiết kế schema cụ thể bằng
SQLAlchemy/Alembic khi triển khai, với FK, unique constraints và indexes theo query.

- Application giữ assignee/state/revision, dữ liệu nhập bất biến và source identity.
- Prediction và submitted review bất biến; decision FK tới đúng cả hai, không tới
  một alias “latest”. Một application chỉ có một final decision.
- Audit mutation ghi cùng transaction. Runtime không có quyền update/delete audit;
  migration credential tách biệt. Không tuyên bố chống can thiệp DB superuser.
- `jobs` là durable PostgreSQL queue: claim bằng transaction ngắn với row lock/
  `SKIP LOCKED`, lease và attempt token. Không giữ DB transaction khi inference.
- Application mutation và job enqueue cùng transaction, nên chưa cần outbox riêng.
- At-least-once execution + idempotent result commits; không hứa exactly-once.
- Connection pool giới hạn theo tài nguyên VPS; không mở PostgreSQL trực tiếp ra ngoài.

## 5. Storage và model serving

Local filesystem adapter có các thao tác nhỏ thực sự cần, ví dụ put/read/delete
theo storage key. Chưa xây plugin system hoặc S3 client. Chỉ operator retention
được xóa artifact không còn tham chiếu, không cung cấp arbitrary delete cho UI.

- DB lưu key/checksum/content metadata, không lưu absolute path VPS.
- Server tạo keys an toàn; không chấp nhận đường dẫn filesystem hoặc URL tải tùy ý.
- Snapshot được ghi atomically, bất biến; file phải tồn tại trước khi DB commit
  tham chiếu tới nó. File mồ côi sau transaction lỗi có thể dọn có kiểm soát.
- Read/download qua authorization, không mount snapshots vào static web root.
- Release pin model version, hash, feature contract, runtime dependencies; model
  mount read-only. Worker kiểm tra bundle trước khi báo scoring-ready.
- Managed PostgreSQL tương lai đổi connection/TLS config; S3 tương lai thêm adapter
  và migrate objects/verify checksums, không đổi business logic.

## 6. Bảo vệ ML và tương thích

`models/select.py` hiện hash YAML trong `configs/`, SQL và một danh sách Python
sources; `verify_lock` còn kiểm tra clean tree và commit identity. Vì thế:

1. Không đưa config deployment/nghiệp vụ mới vào cây protocol YAML cũ một cách vô ý.
2. Không thêm comment/đổi path nguồn bị hash mà coi như không có ảnh hưởng.
3. Không bỏ hash checks, sửa lock cũ hoặc overwrite aggregate references để pass.
4. Refactor có characterization tests cho CLI/API, serialized artifact loading,
   feature order/dtypes và batch/online probabilities.
5. Train lại tạo version/evidence riêng. Không gán kết quả Test cũ cho runtime mới;
   mở Test luôn cần phê duyệt riêng.

API contract 1.1 vẫn là scoring contract hiện có. API nghiệp vụ tương lai dùng
namespace version riêng, không tự đổi `approve/review` thành human decision.
Legacy predict/monitoring/admin endpoints không được public trên VPS mà bỏ qua
authentication. Chi tiết migration: [API contract](../api_contract.md).

Vận hành: [deployment](../operations/deployment.md),
[backup/restore](../operations/backup-restore.md).
