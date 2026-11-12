# Tài liệu RiskLab

RiskLab là **decision-support simulation**, không phải hệ thống phê duyệt tín dụng
thực tế. Tài liệu phân biệt hai trạng thái:

- **Hiện có:** nền tảng ML, scoring API contract 1.1, CLI, tests, Docker và CI.
  Kết quả đã ghi nhận nằm trong [RESULTS.md](RESULTS.md); không phải kết quả vừa chạy lại.
- **Đã bổ sung local:** Next.js shell/login/Admin + workflow UI, business FastAPI
  riêng, PostgreSQL/Alembic, sessions/RBAC, account audit, local storage,
  import/scoring worker, review/decision, báo cáo cơ bản và VPS delivery tooling.
- **Còn triển khai:** deploy VPS/TLS, backup ngoài VPS và nghiệm thu B07 trên
  môi trường thật. Trạng thái task/checks hiện tại xem [PROGRESS](PROGRESS.md).

Không dùng đặc tả mục tiêu làm bằng chứng rằng chức năng đã hoạt động.

## Lộ trình đọc

| Bạn muốn làm gì? | Đọc tài liệu |
|---|---|
| Hiểu sản phẩm và giới hạn MVP | [PRD](product/prd.md) |
| Biết ai được làm gì | [RBAC và separation of duties](product/rbac.md) |
| Hiểu trạng thái và quy tắc chuyển trạng thái | [Workflow](product/workflow.md) |
| Kiểm tra điều kiện hoàn thành | [Acceptance criteria](product/acceptance.md) |
| Chọn task và triển khai từng batch | [MVP task backlog](tasks/README.md) |
| Hiểu kiến trúc ứng dụng mục tiêu | [Architecture](architecture/overview.md) |
| Đọc code và bắt đầu maintain | [Development guide](development/maintaining.md) |
| Chạy web/API/PostgreSQL local | [Application guide](development/application.md) |
| Quay/chạy kịch bản demo synthetic | [Demo script](development/demo.md) |
| Hiểu code ML hiện có | [PROJECT.md](PROJECT.md) |
| Chạy ML/CLI và scoring API hiện có | [OPERATIONS.md](OPERATIONS.md) |
| Chuẩn bị deploy MVP | [Deployment plan](operations/deployment.md) |
| Chuẩn bị khôi phục dữ liệu | [Backup/restore plan](operations/backup-restore.md) |
| Xem quyết định và tiến độ | [Decisions](DECISIONS.md), [Progress](PROGRESS.md) |
| Kiểm tra contract hiện có | [API](api_contract.md), [Data](data_contract.md) |
| Implement contracts B00 | [Business API v1](business_api_contract.md), [Demo import](demo_import_contract.md) — specification, chưa có business endpoints |
| Refactor không phá ML/CLI consumers | [ML compatibility checklist](development/ml-compatibility.md) |
| Đọc bằng chứng và giới hạn ML | [Results](RESULTS.md), [Model card](model_card.md) |

## Quy ước tài liệu

- Hướng dẫn người dùng/developer mới viết bằng tiếng Việt. Code, identifier,
  docstring, comment kỹ thuật và tài liệu agent dùng tiếng Anh.
- README portfolio bằng tiếng Anh. Tài liệu ML lịch sử được giữ nguyên để bảo toàn
  bằng chứng; không dịch hoặc thay metric chỉ để đồng bộ hình thức.
- PRD là nguồn chuẩn về phạm vi; RBAC về quyền; workflow về transition;
  acceptance về nghiệm thu. Tài liệu khác liên kết thay vì sao chép các bảng này.
- Thay đổi quyết định cần bổ sung [decision log](DECISIONS.md), không viết lại lịch sử.
- Coding agent bắt đầu từ [AGENTS.md](../AGENTS.md). Đây là hướng dẫn làm việc,
  không phải một bản kiến trúc hoặc persistent memory khác.
