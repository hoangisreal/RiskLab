# Backup và restore — MVP

**Trạng thái:** có scripts và PostgreSQL rehearsal local. Scheduler, off-VPS
destination/key recovery và RPO/RTO thật chưa nghiệm thu; xem [PROGRESS](../PROGRESS.md).
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
   Báo thành công chỉ sau bước này. Thất bại trả exit khác 0 và giữ bản local vừa xác minh.

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

## Commands hiện có

Các URL files chứa plain `postgresql://` URL, không có suffix SQLAlchemy; quyền
0600. Client dùng URL qua environment, không đưa password lên command argv.
Cần `pg_dump`, `pg_restore`, `psql`, Python 3, `flock`, `tar`, OpenSSL trên host.

```bash
BACKUP_ROOT=/private/backups DATABASE_URL_FILE=/private/dump-url \
  STORAGE_ROOT=/private/objects RELEASE_DIR=/private/release \
  OFFSITE_DIR=/mounted/offsite ENCRYPTION_KEY_FILE=/private/backup-key \
  bash scripts/backup.sh

BACKUP_DIR=/private/backups/backup-ID \
  TARGET_DATABASE_URL_FILE=/private/fresh-target-url \
  TARGET_STORAGE_ROOT=/private/fresh-objects bash scripts/restore.sh
```

Backup ghi vào staging riêng, kiểm tra full inventory/checksums rồi mới công bố.
Offsite được cấu hình thì bắt buộc key, kiểm tra roundtrip giải mã và bytes bản
sao trước khi báo hoàn tất offsite. Bản local được publish và áp retention sau khi
verify, được giữ ngay cả khi mã hóa hoặc chuyển offsite thất bại. Directory hỏng
không được tính là bản thay thế tốt và cần operator kiểm tra/dọn riêng.
Bản plaintext local cần quyền filesystem hạn chế; offsite mount không tự chứng
minh đó là storage độc lập. Archive giải mã chứa thư mục `backup/` và manifest.

Restore từ chối DB có user tables và storage không rỗng, không dùng `--clean`.
Script render toàn bộ archive thành SQL bằng `pg_restore --file` trước khi ghi
objects vào target. Chỉ khi render thành công mới stage objects rồi dùng một lệnh
`psql -X --single-transaction --set ON_ERROR_STOP=1` chạy hai file theo thứ tự:
restore SQL và recovery SQL. Vì vậy restore schema/data, thu hồi sessions và reset
RUNNING leases cùng commit; recovery lỗi không để lại DB đã restore với sessions cũ.
Không pipe SQL trực tiếp từ `pg_restore` sang `psql`: producer có thể lỗi sau khi
consumer đã nhận một phần stream. SQL restore không tự có `BEGIN`/`COMMIT`; transaction
do `psql` sở hữu. Recovery qualify `public.sessions`/`public.jobs` vì SQL dump reset
`search_path`. Không reset `attempt` hoặc tăng retry budget để che các job crash.

Target phải là môi trường cách ly, không có client khác ghi đồng thời; role groups
cần được provision trước theo `deploy/postgres-roles.sql`. Sau restore, quyền worker
và job fencing vẫn phải được kiểm tra trước khi bật workload. Lỗi hoặc mất connection
không được coi là success: giữ target cách ly và xác minh trạng thái, không tự đoán
transaction đã commit hay rollback khi connection bị mất đúng biên commit.

SQL và client diagnostics nằm trong private temporary directory (umask 077), dọn
khi script exit; không in raw SQL/error detail chứa dữ liệu vào stdout/stderr.
`TMPDIR` có thể trỏ tới volume private đủ dung lượng; SQL giải nén có thể lớn hơn
archive. Không tự upload thư mục này; SIGKILL/mất điện có thể để lại staging cần
operator xử lý an toàn. Nếu dùng client container, bind-mount cả private `TMPDIR`.
Đây là restore từ backup/operator tin cậy, không phải sandbox cho arbitrary SQL.

Release bundles trong backup được operator khôi phục riêng sau kiểm tra identity;
script chỉ khôi phục DB và objects. Lỗi giữa các bước có thể để target cần dọn
thủ công; không tự xóa DB hoặc storage để retry.

### Kiểm chứng rollback/recovery local

```bash
uv run pytest tests/business/test_restore_atomicity.py tests/business/test_backup_restore.py
```

Tests dùng database PostgreSQL disposable và Docker client tools: lỗi trigger ở
bước thu hồi sessions hoặc reset leases phải rollback cả schema/data; lỗi render
archive không ghi target objects/DB. Luồng thành công chứng minh sessions = 0,
object checksum khớp, lease cũ không được complete và worker role riêng không vượt
attempt budget. Đây là recovery regression local, không là release rollback/VPS
restore drill hoặc bằng chứng offsite/RPO/RTO.

Nguồn chuẩn PostgreSQL 15: [psql --single-transaction](https://www.postgresql.org/docs/15/app-psql.html)
và [pg_restore --file / warning về trusted dump](https://www.postgresql.org/docs/15/app-pgrestore.html).
