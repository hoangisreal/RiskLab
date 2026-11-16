# RiskLab — bản đồ project

## Project trong một trang

**RiskLab là mô phỏng hỗ trợ quyết định tín dụng** trên dữ liệu Home Credit.
Model chấm xác suất khách hàng gặp khó khăn thanh toán; con người ra quyết
định: Manager import → worker chấm điểm → Analyst review và đề xuất → Manager
chốt quyết định mô phỏng. Admin chỉ quản lý tài khoản. Điểm số model không bao
giờ tự tạo quyết định của con người.

| Khía cạnh | Hiện trạng |
|---|---|
| Ứng dụng | Next.js 16 + FastAPI/PostgreSQL + Python worker; chạy local bằng synthetic data qua `make demo-up` |
| Workflow | Import → scoring bất đồng bộ → phân công → Analyst draft/submit → Manager return/decide; audit cùng transaction với mutation |
| ML | LightGBM đóng băng, contract 1.1, 147 inputs từ application + bureau + previous + installments; threshold `0.08040201`; lock `17b83143e33bb907` |
| Bằng chứng ML | Test ROC-AUC 0.7754 trên 46.127 hồ sơ, run `50d36f3b142a41039ff4d72967242f9a`; chi tiết tại [RESULTS](RESULTS.md) |
| Chưa nghiệm thu | VPS/TLS, release từ registry, backup ngoài VPS — trạng thái thật chỉ ghi tại [PROGRESS](PROGRESS.md) |

```text
Kaggle CSV → ingest Parquet → validate/partition → feature SQL (DuckDB)
→ Train/Calibration/Selection → selection lock → MLflow registry
→ FastAPI scoring → business API + worker → Analyst review → Manager decision
```

Năm partition tách bạch vai trò: Train fit model, Calibration fit calibrator,
Selection chọn model/nguồn/calibration/threshold, Test chỉ mở cho locked
evaluation có authorization riêng, Monitoring chỉ mô phỏng drift.

## Cách đọc theo tầng (cho người và coding agent)

| Tầng | Đọc khi nào | Nội dung |
|---|---|---|
| 0 — luôn đọc | Mọi task | [AGENTS.md](../AGENTS.md), trang này, phần đầu [PROGRESS](PROGRESS.md) |
| 1 — theo task | Sửa đúng miền đó | Contract và guide liên quan trong bảng dưới |
| 2 — bằng chứng | Kiểm chứng kết quả, sửa ML | [RESULTS](RESULTS.md), [model card](model_card.md), [DECISIONS](DECISIONS.md) — đọc đúng mục, không đọc toàn bộ |
| 3 — lịch sử | Audit, tranh chấp số liệu | [archive](archive/experiments.md), ảnh UI — không đọc mặc định |

Không suy ra acceptance từ đặc tả hoặc trạng thái task; kết quả thực chạy,
phạm vi và blockers chỉ ghi tại [PROGRESS](PROGRESS.md).

## Bản đồ tài liệu

| Nhu cầu | Nguồn chuẩn |
|---|---|
| Kiến trúc ứng dụng và entrypoint đang chạy | [Kiến trúc](architecture/overview.md) |
| Cài/chạy web, API, worker, PostgreSQL và tests | [Ứng dụng local](development/application.md) |
| Review/sửa code, chọn checks | [Maintain](development/maintaining.md) |
| Chạy demo ba vai trò bằng synthetic data | [Demo](development/demo.md) |
| Phạm vi, quyền, state, tiêu chí nghiệm thu | [PRD](product/prd.md), [RBAC](product/rbac.md), [workflow](product/workflow.md), [AC](product/acceptance.md) |
| Request/response business và định dạng import | [Business API v1](business_api_contract.md), [demo import](demo_import_contract.md) |
| Contract scoring ML và dữ liệu thô | [Scoring API](api_contract.md), [data contract](data_contract.md) |
| ML modules, commands và tương thích artifact | [PROJECT](PROJECT.md), [OPERATIONS](OPERATIONS.md), [compatibility](development/ml-compatibility.md) |
| Kết quả ML đã ghi nhận, giới hạn model | [RESULTS](RESULTS.md), [model card](model_card.md) |
| Release, backup và khôi phục | [Deployment](operations/deployment.md), [backup/restore](operations/backup-restore.md) |
| Việc còn mở và lý do các quyết định | [Backlog](tasks/README.md), [DECISIONS](DECISIONS.md) |
| Giao việc cho agent/người khác | [Handoff template](tasks/handoff-template.md) |

Phân biệt hai tài liệu dễ nhầm: [OPERATIONS](OPERATIONS.md) là vận hành stack
ML/scoring local; [operations/deployment](operations/deployment.md) là kế
hoạch deploy ứng dụng lên VPS (chưa nghiệm thu).

## Quy ước tài liệu

Hướng dẫn mới bằng tiếng Việt; portfolio README và bằng chứng ML lịch sử bằng
tiếng Anh; code và agent instructions bằng tiếng Anh. Liên kết nguồn chuẩn
thay vì chép lại quy tắc. Giữ nguyên bằng chứng ML lịch sử, lock, reference
và metric; quyết định mới bổ sung vào [DECISIONS](DECISIONS.md), kết quả kiểm
chứng mới cập nhật [PROGRESS](PROGRESS.md). Không mở Test partition cho các
sửa đổi thông thường.
