# Kiến trúc RiskLab hiện có

Ứng dụng đã chạy local. Mô hình VPS bên dưới là topology mục tiêu, chưa phải
bằng chứng deploy. Trạng thái và checks: [PROGRESS](../PROGRESS.md).
Phạm vi/quyền/state: [PRD](../product/prd.md), [RBAC](../product/rbac.md),
[workflow](../product/workflow.md).

## 1. Deployment view

```text
Browser → Caddy (VPS: HTTPS, cùng origin)
            ├── /        → Next.js
            └── /api/v1/ → FastAPI → PostgreSQL
                               │          ↑
                               ↓          │
                          LocalStorage ← Python worker
Training CLI → MLflow registry → model bundle được operator ghim
```

FastAPI và worker dùng chung codebase/image, khác command. PostgreSQL giữ dữ liệu
nghiệp vụ và queue; DuckDB/Parquet phục vụ ML. Không Redis/Celery, microservices,
SSO hay Kubernetes. MLflow/training chạy riêng, không public qua Caddy.

## 2. Ranh giới code

| Đường dẫn/entrypoint thực tế | Trách nhiệm |
|---|---|
| `apps/web/src/` | UI ba role, API client cùng origin; không sở hữu authorization |
| `src/risklab/business/main.py:create_app` | Business API `/api/v1`, accounts, import/review/decision/report |
| `src/risklab/business/worker.py` (`risklab-worker`) | Claim/heartbeat/retry import và scoring, commit có attempt fencing |
| `src/risklab/business/scoring.py` | Kiểm tra pinned artifact/protocol/runtime, dùng shared inference |
| `src/risklab/api/` | Legacy scoring contract 1.1, metadata, monitoring, reload; entrypoint riêng |
| `src/risklab/{data,features,models,experiments,registry,monitoring}/` | Pipeline ML; bản đồ chi tiết ở [PROJECT](../PROJECT.md) |
| `migrations/` | Alembic schema và quyền DB; head chuẩn tại `business/db.py` |
| `deploy/`, `scripts/{deploy,backup,restore}.sh` | Compose/Caddy, preflight và backup/restore local |

## 3. Authentication và UI

FastAPI kiểm tra session, account hiện hành, role và quyền trên hồ sơ. UI guards
chỉ phục vụ trải nghiệm. Request cá nhân không cache; cookie và CSRF ở cùng
origin. Draft conflict yêu cầu người dùng tải lại rõ ràng; upload retry giữ key
cho cùng intent. Contract chi tiết: [Business API](../business_api_contract.md).

## 4. Persistence và jobs

`users`, `sessions`, `imports`, `applications`, `jobs`, `predictions`, `reviews`,
`decisions`, `audit_events` và idempotency tables nằm trong PostgreSQL. API,
worker và migration dùng quyền riêng; worker không có quyền quyết định nghiệp vụ.

Mutation và audit cùng transaction. Application mutation khóa hàng rồi kiểm tra
revision. Worker claim bằng `SKIP LOCKED`, tính toán ngoài transaction, giữ lease
bằng heartbeat và khóa/kiểm tra attempt trước commit. Crash cũng tiêu thụ retry
budget. At-least-once execution không đồng nghĩa exactly-once processing.

## 5. Storage và model serving

`LocalStorage.put/read` dùng key SHA256, immutable file và kiểm tra checksum;
không có public static snapshot hoặc arbitrary delete API. Object được ghi trước
DB reference; từ chối upload không đủ quyền trước khi ghi file.

Release ghim hash model, protocol, feature columns/groups và dependency versions.
Scoring từ chối metadata không khớp. Legacy registry phục vụ đúng evaluated
artifact đã kiểm tra hash. Model output, đề xuất Analyst và decision Manager là
ba đối tượng độc lập.

## 6. Bảo vệ ML và tương thích

Giữ các ML protocol hashes, clean-tree/commit guards và evidence lịch sử; thay
đổi business code không cấp quyền re-lock hoặc mở Test. Chi tiết tại
[ML compatibility](../development/ml-compatibility.md), không lặp lại tại đây.

Chạy local: [application](../development/application.md). Vận hành:
[deployment](../operations/deployment.md), [backup/restore](../operations/backup-restore.md).
