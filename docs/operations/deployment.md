# Infrastructure và deployment plan — MVP

**Trạng thái:** đã có Compose/Caddy, images và CI local; chưa deploy VPS.
`scripts/deploy.sh` hỗ trợ preflight read-only theo manifest v2; `--execute` dừng với exit 2 vì chưa có
quy trình tự động áp manifest/backup/maintenance đã nghiệm thu. Không coi health
của release cũ là deploy thành công. Kết quả: [PROGRESS](../PROGRESS.md).
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
- `deploy/secrets/` và `.secrets/` không vào Git hoặc root Docker context.
  Frontend có `.dockerignore` riêng để loại `.env*`, `.secrets`, dependencies,
  build cache và browser traces trước `COPY . .`; không suy từ `.gitignore` rằng
  Docker tự loại các file này. Ignore rules không thay quyền filesystem/secrets policy.
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

API hiện có `/api/v1/health/live` và `/api/v1/health/ready`; readiness kiểm tra DB/schema,
không chứng minh worker/model sẵn sàng. Worker có job heartbeat nhưng chưa có endpoint readiness riêng. Health public không lộ connection strings hoặc
metadata nhạy cảm. Worker lỗi vẫn có thể cho đọc hồ sơ; UI phải báo scoring
unavailable/pending, không tạo thành công giả. Healthcheck không tự thay thế cơ chế
worker recovery hoặc restart khi process không thoát.

Structured JSON logs có UTC timestamp, service, level, request/job ID, action,
result, latency/error code và actor/resource ID cần thiết. Không log payload,
password, cookies, token hoặc header secrets. Bounded retention/rotation tránh
đầy disk. Audit nghiệp vụ trong PostgreSQL không bị thay bằng stdout logs.

## 5. CI/CD mục tiêu

CI config đã có Python, PostgreSQL, frontend và browser E2E. Hosted run và release
pipeline chưa được kiểm chứng; quy trình release dưới đây cần operator thực hiện:

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
monitoring, reload API trực tiếp qua Caddy. Caddy config đã chặn legacy routes; network/TLS phải kiểm chứng trên VPS thật.

Nghiệm thu vận hành: [AC-17–AC-21](../product/acceptance.md).

## 8. Preflight và thao tác operator

```bash
# Chỉ kiểm tra template; output ghi EXAMPLE ONLY, không là release candidate.
bash scripts/deploy.sh --dry-run --example

# Thay bằng paths/origin của release do operator cung cấp, không dùng secrets làm argv.
RELEASE_MANIFEST=/secure/releases/candidate/manifest.json \
RELEASE_DIR=/secure/releases/candidate/bundle \
BACKUP_ROOT=/secure/backups \
RISKLAB_ORIGIN=https://risklab.example SITE_ADDRESS=risklab.example \
RISKLAB_TRUSTED_PROXY_IPS=172.30.0.10 \
  bash scripts/deploy.sh --dry-run
```

`RISKLAB_TRUSTED_PROXY_IPS` là IP/CIDR peer Caddy do operator xác minh trong
Docker network thực tế. Giá trị trong ví dụ cần thay bằng peer đúng của môi trường.
Compose truyền thành `FORWARDED_ALLOW_IPS` cho Uvicorn. Preflight từ chối wildcard
và CIDR toàn Internet. API chỉ nhận forwarded client IP từ peer được tin cậy để
rate-limit theo client; peer khác vẫn dùng địa chỉ socket. Caddy mặc định tạo
forwarded headers và bỏ giá trị incoming không tin cậy. Kiểm tra hai client qua
Caddy thực trong B07. [Uvicorn settings](https://uvicorn.dev/settings/),
[Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Preflight dùng Python standard library và Docker Compose CLI, không cần load model
hoặc truy cập database/SSH. Chạy từ thư mục khác vẫn dùng đúng Compose của repo.
Docker Compose CLI thiếu hoặc lỗi là **failure**, không bỏ qua. Output Compose được
capture để không in environment/diagnostic có thể chứa thông tin nhạy cảm.

- `risklab-release/2` yêu cầu đúng các field trong
  [manifest mẫu](../../deploy/release-manifest.example.json). Ngoài model/catalog,
  manifest pin cả SHA256 của `release.json` (threshold/policy/protocol/runtime metadata)
  và `comparison.json`. V1 bị reject rõ ràng vì chưa bind hai file này; không sửa
  historical lock/bundle để ép tương thích. Đây là version của deployment manifest,
  không đổi scoring contract 1.1 hoặc ML selection protocol.
  Manifest là input operator tin cậy; checksum chứng minh bytes khớp input này,
  không tự chứng minh người phát hành/registry đáng tin hoặc artifact an toàn.
- Manifest thật không nhận all-zero hashes hoặc image/model placeholder. Không có
  `--example`, template mặc định **exit 2** thay vì báo một release giả đã hợp lệ.
- `api`, `worker`, `migrate` phải render đúng API image digest; `web` đúng web digest;
  catalog checksum đúng manifest, migration/worker command đúng entrypoint, các
  service khác Caddy không publish port, không dùng host networking; Caddy chỉ
  publish TCP 80/443. Không dùng image mẫu thay manifest thật.
- Compose được render bằng `--env-file /dev/null`, `-f` và project directory cố
  định; ambient `COMPOSE_*` bị loại, image/catalog env được lấy từ manifest. Không
  dùng `.env` ngoài ý muốn hoặc override file để thay nội dung đang kiểm tra.
- `RELEASE_DIR` nếu có phải chứa `model.pkl`, `catalog.json`, `release.json`,
  `comparison.json`: kiểm tra từng checksum và đối chiếu model/catalog/feature
  contract identity. Không deserialize pickle. Symlink inputs/ancestors bị từ chối.
- `BACKUP_ROOT` nếu có phải có ít nhất một bản hoàn chỉnh, inventory/checksum đúng,
  dump không rỗng, backup ID trùng tên và timestamp UTC không ở tương lai. Bản mới
  nhưng hỏng không che bản hợp lệ cũ. Default age ≤ 3600 giây, có thể cấu hình
  `BACKUP_MAX_AGE_SECONDS` trong khoảng 1–86400; không dùng file mtime làm evidence.
- Thiếu bundle hoặc backup input: output ghi **NOT CHECKED**, không ngầm coi đạt.
  Exit 0 chỉ xác nhận các checks được liệt kê; không thay deployment approval.

**Vẫn chưa chứng minh:** image có trên registry/đúng build source, runtime/protocol
load trong image, schema DB thực, secrets/volume provision, backup off-VPS và khả
năng restore, maintenance/drain, migration execution/failure, health/workflow sau
deploy, TLS, rollback. `BACKUP_ROOT` checksum pass không chứng minh dump restore được.
Không gỡ `--execute` guard hoặc đóng B06-T03/B07 chỉ từ preflight pass.

Trước deploy được ủy quyền: provision riêng API/worker login và secrets, đặt
`RISKLAB_API_IMAGE`/`RISKLAB_WEB_IMAGE` đúng digest trong manifest; chuẩn bị volume
release với catalog, model và release metadata đã kiểm tra hash. Cùng API image
được dùng cho `api`, `migrate`, `worker`; Compose có command riêng cho hai service
sau, không dựa vào Docker build target để thay CMD của image đã pull.

Operator dừng mutations/worker, backup và kiểm tra tuổi bản sao; pull đúng images,
chạy migration one-shot, khởi động và kiểm tra schema/model + workflow synthetic
trước khi mở ghi. Lỗi bất kỳ bước nào phải dừng; rollback dùng manifest cũ chỉ khi
schema/bundle tương thích. Chưa có SSH/CD tự động thay các bước này.

### Cơ sở kỹ thuật đã đối chiếu

- [Docker Compose config](https://docs.docker.com/reference/cli/docker/compose/config/)
  render model sau interpolation; không khởi động service hay chứng minh health.
- [Compose interpolation precedence](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
  giải thích ưu tiên shell/`--env-file`/`.env`, lý do preflight ghim inputs thay vì
  phụ thuộc environment ngầm.
- [Docker Compose run](https://docs.docker.com/reference/cli/docker/compose/run/)
  là one-off container; migration runner tương lai cần kiểm tra exit status của
  chính lệnh migration. Không fallback sang health release cũ khi migration fail.
- [Docker build context/.dockerignore](https://docs.docker.com/build/concepts/context/#dockerignore-files)
  giải thích file loại bỏ phải nằm ở root của từng build context; vì web build
  context là `apps/web`, ignore file của repo root không bảo vệ context đó.
