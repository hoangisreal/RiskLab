# RiskLab — bản đồ project

**RiskLab là mô phỏng hỗ trợ quyết định tín dụng.** Analyst đề xuất; Manager chốt;
Admin quản lý tài khoản. Điểm số model không tạo quyết định của con người.

**Hiện có:** ML/scoring contract 1.1; ứng dụng Next.js + FastAPI/PostgreSQL;
import → worker scoring → review → decision; báo cáo; CI và công cụ vận hành local.

**Chưa nghiệm thu:** VPS/TLS, release từ registry và backup ngoài VPS. Kết quả thực chạy, phạm vi và blockers chỉ ghi tại
[PROGRESS](PROGRESS.md); không suy ra acceptance từ đặc tả hoặc task DONE.

## Đọc nhanh theo nhu cầu

| Nhu cầu | Nguồn chuẩn |
|---|---|
| Nắm code đang chạy và các entrypoint | [Kiến trúc](architecture/overview.md) |
| Cài/chạy web, API, worker, PostgreSQL và tests | [Ứng dụng local](development/application.md) |
| Review/sửa code, chọn checks | [Maintain](development/maintaining.md) |
| Chạy demo ba vai trò bằng synthetic data | [Demo](development/demo.md) |
| Phạm vi, quyền, state, tiêu chí nghiệm thu | [PRD](product/prd.md), [RBAC](product/rbac.md), [workflow](product/workflow.md), [AC](product/acceptance.md) |
| Request/response và import format | [Business API v1](business_api_contract.md), [demo import](demo_import_contract.md) |
| ML modules, commands và tương thích artifact | [PROJECT](PROJECT.md), [OPERATIONS](OPERATIONS.md), [compatibility](development/ml-compatibility.md) |
| Contract scoring và dữ liệu | [Scoring API](api_contract.md), [data](data_contract.md) |
| Kết quả ML đã ghi nhận, giới hạn model | [RESULTS](RESULTS.md), [model card](model_card.md) |
| Release, backup và khôi phục | [Deployment](operations/deployment.md), [backup/restore](operations/backup-restore.md) |
| Task và lý do các quyết định | [Backlog](tasks/README.md), [DECISIONS](DECISIONS.md) |

## Context để tiếp tục công việc

Đọc trang này → trạng thái đầu [PROGRESS](PROGRESS.md) → contract và code liên quan
đến task. Agent tuân theo [AGENTS.md](../AGENTS.md); giao việc dùng
[handoff template](tasks/handoff-template.md). Không cần đọc lại toàn bộ lịch sử
thử nghiệm để sửa một endpoint hoặc màn hình.

Hướng dẫn mới bằng tiếng Việt; portfolio README bằng tiếng Anh; code và agent
instructions bằng tiếng Anh. Liên kết nguồn chuẩn thay vì chép lại quy tắc.
Giữ nguyên bằng chứng ML lịch sử và bổ sung quyết định mới, không ghi đè lock,
reference hoặc metric. Không mở Test partition để kiểm tra các sửa đổi thông thường.
