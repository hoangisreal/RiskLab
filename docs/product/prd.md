# PRD — RiskLab MVP

**Trạng thái:** phạm vi MVP đã chốt ngày 2026-11-10. Implementation và bằng chứng
local/VPS được ghi riêng tại [PROGRESS](../PROGRESS.md). Nền tảng ML hiện có được mô tả tại [PROJECT.md](../PROJECT.md).

## 1. Mục tiêu và người dùng

RiskLab giúp chuyên viên xem hồ sơ demo, kiểm tra risk prediction và explanation,
ghi nhận review, rồi chuyển cho Manager quyết định. Mọi quyết định là **mô phỏng**;
không kết nối ngân hàng, giải ngân hoặc tự động phê duyệt tín dụng thực tế.

| Vai trò | Trách nhiệm |
|---|---|
| Analyst | Xem hồ sơ được giao, đánh giá, ghi chú và đề xuất |
| Manager | Import, phân công, trả lại review hoặc chốt quyết định mô phỏng |
| Admin | Quản lý tài khoản và phân quyền; không mặc nhiên có quyền nghiệp vụ |

ML Engineer dùng CLI/pipeline hiện có; không xây role/portal ML riêng trong MVP.
Quyền chi tiết: [RBAC](rbac.md). Trạng thái: [workflow](workflow.md).

## 2. Workflow demo chính

1. Manager đăng nhập, import bộ hồ sơ demo hoặc CSV đúng contract.
2. Worker validate, kiểm tra nguồn features, scoring bằng model bundle đã pin.
3. Manager phân công hồ sơ cho Analyst đang hoạt động.
4. Analyst xem dữ liệu, score, warnings, explanation và model identity.
5. Analyst ghi nhận đánh giá và submit đề xuất.
6. Manager xem review, trả lại kèm lý do hoặc chốt quyết định mô phỏng.
7. Lịch sử thể hiện ai thực hiện, thời điểm, prediction và model đã được sử dụng.

## 3. Trong phạm vi

- Đăng nhập/đăng xuất, tài khoản nội bộ do Admin quản lý, RBAC phía server.
- Danh sách hồ sơ, tìm kiếm theo mã hồ sơ, lọc trạng thái/người phụ trách, phân trang.
- Import demo/CSV có giới hạn số dòng, dung lượng và báo cáo lỗi.
- Feature availability, scoring bất đồng bộ, explanation và trạng thái lỗi.
- Phân công, ghi chú, đề xuất, Manager quyết định, lịch sử và audit.
- Thống kê cơ bản theo trạng thái và workload, không xây BI engine.
- Hiển thị model hiện hành và báo cáo so sánh model đã có.
- Next.js + FastAPI + PostgreSQL + Python worker, Docker Compose trên một VPS,
  Caddy HTTPS, GitHub Actions, health checks, logs, secrets, backup/restore.
- Automated tests và một kịch bản/video demo workflow hoàn chỉnh.

Import nhiều dòng cần worker xử lý từng hồ sơ; điều này không mở rộng thành một
sản phẩm batch scoring độc lập có scheduling hoặc dashboard nâng cao.

## 4. Ngoài phạm vi

- Analyst tự chốt; tự phê duyệt theo threshold; phê duyệt nhiều cấp/ủy quyền.
- Sửa hoặc mở lại quyết định cuối; sửa trực tiếp dữ liệu tài chính sau import.
- Public signup, SSO/OIDC, multi-tenant, thông báo email/SMS.
- Retraining trên web, model rollback UI, dashboard drift/comparison nâng cao.
- Kubernetes, Terraform, microservices, Kafka, Redis/Celery hoặc feature store
  khi chưa có nhu cầu thực tế được chấp thuận.
- Managed PostgreSQL/S3 deployment ngay trong MVP; chỉ chuẩn bị configuration và
  interface storage nhỏ để chuyển đổi sau.

Monitoring, challenger experiments, rollback CLI và load scripts hiện có được
giữ nguyên; không bắt buộc tích hợp lên UI. Không xóa chúng vì nằm ngoài MVP web.

## 5. Dữ liệu và giới hạn ML

- Public demo mặc định **synthetic-only**. Anonymized data chỉ được dùng sau khi
  kiểm tra quyền phân phối và nguy cơ tái nhận dạng; bỏ tên/ID là chưa đủ.
- Không public Kaggle raw data, row-level reports, credentials hoặc MLflow UI.
- `TARGET` là payment difficulties theo Home Credit, không phải xác suất vỡ nợ
  thực tế đã được kiểm định. `application_test.csv` không có nhãn, không phải
  Test partition nội bộ.
- Không dùng form chỉ có tuổi/thu nhập để giả định đủ historical features.
  B00 chốt [demo-import/1](../demo_import_contract.md): CSV chỉ tham chiếu snapshot
  synthetic tin cậy trong catalog phía server. Không upload raw history/features
  tùy ý; snapshot giữ raw scoring contract và được kiểm tra trước khi sử dụng.
  [Business API v1](../business_api_contract.md) là specification cho B01–B04,
  trạng thái kiểm chứng từng phần nằm trong PROGRESS.
- Phân biệt nguồn chưa được cung cấp với nguồn đã xác nhận không có lịch sử.
  Thiếu nguồn bắt buộc chặn scoring; không tự điền rỗng để vượt validation.
- Explanation mô tả đóng góp của model, không chứng minh nhân quả hoặc cung cấp
  lý do pháp lý để từ chối tín dụng. Phải hiển thị không gian log-odds và giới hạn.
- Đề xuất Analyst, output model và quyết định Manager là ba khái niệm khác nhau.

## 6. Nguyên tắc hoàn thành

Nghiệm thu theo [AC-01–AC-21](acceptance.md), không theo số thư mục hoặc công nghệ.
Ưu tiên lát cắt import → score → review → decision chạy xuyên suốt, rồi hoàn thiện
vận hành. Không có cam kết HA hoặc latency SLO chưa được đo.

Training lại được phép khi cần, nhưng phải giữ evidence cũ và version mới riêng.
Không tự mở lại Test hoặc gán metrics cũ cho pipeline mới. Thay đổi protocol
hash cần migration có kiểm tra, không được vô hiệu hóa integrity gates.
