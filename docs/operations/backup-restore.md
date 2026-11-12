# Backup và restore — MVP

**Trạng thái:** yêu cầu vận hành cho VPS tương lai; chưa có scheduler, backup hoặc
restore drill được thực hiện bởi thay đổi tài liệu này.
Xem [deployment plan](deployment.md) và [acceptance](../product/acceptance.md).

## Phạm vi và mục tiêu

- PostgreSQL: accounts, applications, jobs, predictions, reviews, decisions, audit.
- Storage: snapshot/input cần tái dựng prediction, import source cần giữ, model
  bundle đang phục vụ và bundle trước để phục hồi release tương thích.
- Manifest: schema revision, app image digest, model hash, object inventory/checksums,
  thời điểm UTC và công cụ/phiên bản backup.
- Deployment config cần để dựng lại; secrets/keys có quy trình khôi phục riêng,
  không chép plaintext secrets vào archive hoặc repository.

Mục tiêu RPO ≤ 24 giờ, phụ thuộc backup thành công và được đưa ra ngoài VPS.
RTO chưa cam kết: đo và ghi lại qua restore drill. Một bản trên cùng disk VPS
không được tính là disaster-recovery backup.

## Lịch và retention

- Backup DB và snapshot/artifact liên quan hằng ngày, thêm bản trước migration.
- Giữ 7 bản ngày và 4 bản tuần; chỉ prune sau khi xác minh bản thay thế tốt.
- Mã hóa trước khi chuyển tới storage ngoài VPS; encryption key phải khôi phục
  được độc lập, không chỉ nằm trên VPS bị mất.
- Ghi timestamp/status/checksum, giám sát backup failure và bản mới nhất quá hạn.
  Không chỉ kiểm tra scheduler đã chạy mà bỏ qua upload thất bại.

## Quy trình backup nhất quán

1. Bật maintenance cho mutations; cho request đang chạy hoàn tất, drain/dừng
   worker an toàn. Chưa cho import/scoring mới ghi DB hoặc storage.
2. Tạo PostgreSQL logical backup bằng công cụ tương thích phiên bản DB, ví dụ
   custom-format `pg_dump`; không copy live database volume như một backup hợp lệ.
3. Sao chép immutable objects/model bundles được DB tham chiếu và manifest checksum.
   Không chạy storage garbage collection trong cửa sổ backup.
4. Xác nhận dump/artifacts đọc được, checksum/inventory đầy đủ; kết thúc snapshot
   và mở lại mutations/worker. Ghi thời điểm bắt đầu/kết thúc consistency window.
5. Mã hóa và chuyển archive ra ngoài VPS; xác minh checksum và khả năng truy cập.
   Báo thành công chỉ sau bước này. Thất bại không xóa bản tốt trước đó.

Maintenance window ngắn là lựa chọn KISS cho MVP một VPS; chưa xây distributed
snapshot hoặc PITR. Khi workload lớn hơn mới xem xét thay đổi chính sách.

## Restore drill

1. Dựng môi trường **cách ly** sạch, không trỏ vào DB production hoặc gửi job ra
   ngoài. Chọn một backup hoàn chỉnh cùng manifest.
2. Lấy key qua kênh riêng, giải mã và kiểm tra checksum/inventory.
3. Restore PostgreSQL vào database mới bằng công cụ tương thích; khôi phục runtime
   roles/grants theo cấu hình, không vô tình cấp superuser cho ứng dụng.
4. Restore đúng storage keys/model bundles và quyền filesystem. Kiểm tra mọi
   snapshot được DB tham chiếu tồn tại và hash khớp.
5. Chạy app image tương thích schema trong manifest, chưa tự migrate lên latest.
6. Thu hồi session đã restore; đưa jobs đang RUNNING về quy trình lease recovery
   có kiểm soát trước khi bật worker. Idempotency phải ngăn lặp side effects.
7. Kiểm tra login, RBAC, counts/FK, lịch sử decision/audit, model integrity; chạy
   workflow synthetic mới và thử worker retry.
8. Ghi thời gian phục hồi thực tế, backup age/data loss window, lỗi và kết quả;
   không gọi restore thành công chỉ vì `pg_restore` exit 0.

Restore production cần operator phê duyệt và maintenance; không ghi đè DB đang
chạy khi drill. Secrets đổi sau backup vẫn phải được lấy từ nguồn hiện hành.

## Evidence nghiệm thu

Lưu báo cáo tổng hợp (không chứa row-level data hoặc secrets): backup ID/time,
app/schema/model identity, checksum verification, ngày restore, môi trường,
thời gian phục hồi, checks đã chạy và failures. Chỉ đánh dấu AC-18 đạt sau drill.
