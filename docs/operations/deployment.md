# Infrastructure và deployment plan — MVP

**Trạng thái:** đặc tả mục tiêu, chưa có production Compose/Caddy/deploy workflow.
`docker-compose.yml` hiện tại chỉ chạy MLflow, scoring API và training jobs local;
không sử dụng nó như cấu hình public demo đã được harden.
Lệnh chạy hiện có nằm tại [OPERATIONS.md](../OPERATIONS.md).

## 1. Một VPS Linux

| Service | Vai trò | Public port |
|---|---|---|
| Caddy | TLS/HTTPS, reverse proxy cùng origin | 80/443 |
| Next.js `web` | Production frontend build | Không |
| FastAPI `api` | Nghiệp vụ, authorization, API | Không |
| Python `worker` | Import/scoring | Không |
| PostgreSQL | Nghiệp vụ/session/jobs | Không |

Migration là one-shot command. MLflow/training không bắt buộc chạy thường trực
trên VPS phục vụ. Không thêm Kubernetes, Terraform, Redis hoặc nhiều server.

Một VPS là single point of failure; downtime khi maintenance được chấp nhận,
không tuyên bố HA hoặc zero-downtime deployment.

## 2. Chuẩn bị và hardening

- Domain/DNS trỏ tới VPS, firewall chỉ mở web và SSH quản trị cần thiết.
- SSH key-based, hạn chế nguồn truy cập khi khả thi, không public credential Admin.
- Chỉ Caddy publish port; mạng Docker nội bộ cho DB/API/web.
- Image production có version/digest, chạy non-root khi phù hợp, không mount
  Docker socket hoặc toàn repository chứa secrets vào app.
- Persistent volumes cho PostgreSQL, Caddy certificate state và artifact storage.
- Model bundle read-only; cấp quyền write tối thiểu cho worker storage.
- Resource/connection limits, log rotation và restart policy theo tài nguyên VPS.
- Đặt caps cho upload rows/bytes, concurrent jobs, retries và login attempts;
  giá trị cụ thể phải được kiểm tra bằng demo workload trước release.

## 3. Configuration và secrets

- Database qua connection URL, pool/TLS settings; business code không hard-code
  hostname Compose. Local storage root qua config, DB chỉ lưu storage keys.
- Secrets nằm ngoài repository trên VPS, quyền đọc hạn chế theo service/user.
  Mount secret files và hỗ trợ `*_FILE` khi triển khai; `.env.example` chỉ placeholders.
- Không bake secret vào image, build args, Next.js `NEXT_PUBLIC_*`, logs hoặc CI artifacts.
- Compose secrets không tự cung cấp encrypted secret manager; host filesystem,
  backup encryption và quyền truy cập vẫn phải được quản lý.
- DB runtime credentials khác migration credentials. Deploy SSH credential nằm
  trong GitHub environment secrets; quyền truy cập host vẫn là đặc quyền cao.
- Runbook rotation phải bao gồm thay secret, restart có kiểm soát, thu hồi credential
  cũ và session liên quan; không public Admin bootstrap password.

## 4. Health checks và logs

| Check | Ý nghĩa |
|---|---|
| Web HTTP health | Frontend process phục vụ được |
| API liveness | Process sống, không phụ thuộc mọi downstream |
| API readiness | DB truy cập được, schema tương thích |
| PostgreSQL readiness | DB nhận connection |
| Worker heartbeat/model readiness | Worker còn tiến triển và model hợp lệ |

Đường dẫn health cụ thể sẽ được đưa vào API contract khi triển khai; đây không
phải các endpoints đã tồn tại. Health public không lộ connection strings hoặc
metadata nhạy cảm. Worker lỗi vẫn có thể cho đọc hồ sơ; UI phải báo scoring
unavailable/pending, không tạo thành công giả. Healthcheck không tự thay thế cơ chế
worker recovery hoặc restart khi process không thoát.

Structured JSON logs có UTC timestamp, service, level, request/job ID, action,
result, latency/error code và actor/resource ID cần thiết. Không log payload,
password, cookies, token hoặc header secrets. Bounded retention/rotation tránh
đầy disk. Audit nghiệp vụ trong PostgreSQL không bị thay bằng stdout logs.

## 5. CI/CD mục tiêu

CI hiện tại chỉ kiểm tra Python. Bổ sung theo tiến độ code thực tế, không tạo
test/build placeholders rồi coi chúng là đã pass:

1. PR: Python lint/typecheck/tests, PostgreSQL integration/migrations, frontend
   lint/typecheck/build và synthetic E2E cho các vai trò.
2. Protected release: build image một lần từ commit được chọn, gắn SHA/digest,
   publish registry; frontend/backend/worker và model bundle có release manifest.
3. GitHub environment approval; serialize deploy để tránh hai migration cùng chạy.
4. Trên VPS: kiểm tra disk/secrets/backup freshness; pull đúng image digest.
5. Dừng nhận mutation/jobs trong maintenance window, drain hoặc dừng worker an toàn;
   backup nhất quán theo [backup/restore](backup-restore.md), chạy migration one-shot.
6. Khởi động release, kiểm tra health/readiness và smoke workflow bằng synthetic
   data; chỉ mở lại ghi dữ liệu khi đã xác nhận schema/model tương thích.
7. Lưu release ID, image/model digest, migration revision và kết quả deploy.

Workflow phải dùng action pin theo commit, quyền tối thiểu, không đưa input PR vào
shell trực tiếp, không cấp deploy secrets cho fork/untrusted PR hoặc chạy code PR
đặc quyền qua `pull_request_target`. Kiểm tra SSH host key, không tắt verification.

## 6. Migration và rollback

- Kiểm tra tạo DB mới và upgrade từ revision trước trên PostgreSQL thực.
- Ưu tiên backward-compatible migrations; destructive migration cần quyết định riêng.
- Backup trước migration. Không chạy migration tự động trong mọi API replica startup.
- Rollback ứng dụng/model bằng manifest cũ chỉ khi schema và bundle vẫn tương thích.
- Không tự `downgrade` DB để che lỗi. Nếu migration không tương thích: maintenance,
  operator quyết định roll-forward hoặc restore đã kiểm chứng; ghi rõ dữ liệu có
  thể mất sau backup. Không hứa image rollback sẽ hoàn tác database.
- Model rollback không thay đổi prediction/decision lịch sử hoặc bỏ integrity checks.

## 7. Public demo

Mặc định synthetic-only; không mount Kaggle raw data, prediction rows hoặc MLflow
UI ra Internet. Anonymized data cần xác nhận quyền sử dụng/phân phối và đánh giá
tái nhận dạng trước. Scoring input không chứa `TARGET`.

Không public Admin credential; demo Analyst/Manager nếu chia sẻ phải giới hạn trên
synthetic data và có caps tài nguyên. Không đưa legacy unauthenticated scoring,
monitoring, reload API trực tiếp qua Caddy. Đây là requirement triển khai, không
phải bảo vệ đã có sẵn trong source hiện tại.

Nghiệm thu vận hành: [AC-17–AC-21](../product/acceptance.md).
