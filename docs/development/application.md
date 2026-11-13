# Chạy RiskLab local

Cần Python 3.12 qua `uv`, Node.js 24 và Docker daemon. Stack gồm PostgreSQL,
business FastAPI, worker và Next.js. Contracts tại [Business API](../business_api_contract.md),
[import](../demo_import_contract.md). Trạng thái/checks tại [PROGRESS](../PROGRESS.md).

## Khởi động đủ stack synthetic

```bash
make setup
cd apps/web && npm ci --ignore-scripts
cd ../..
make app-local
make app-status
```

Mở **http://localhost:3000** để Origin khớp CSRF. `app-local` lần lượt provision DB,
migrate, chuẩn bị model/catalog, build và chạy API/worker/standalone web ở nền.
Các cổng loopback là DB 15432, API 8000, web 3000.

| Tài nguyên local | Vị trí |
|---|---|
| Admin/Manager/Analyst | `local.admin`, `local.manager`, `local.analyst` |
| Mật khẩu ngẫu nhiên, owner-only | `.secrets/local_demo_accounts.json` |
| CSV để Manager import | `data/application/local-demo/import.csv` |
| Model/catalog/configs/objects synthetic | `data/application/local-demo/` |
| Process state, private logs, ảnh smoke khi có | `reports/operations/application-local/` |

Model `synthetic-local-v1` fit 500 Train rows synthetic bằng shared features.
Catalog có 3 hồ sơ, 2 đủ dữ liệu để scoring và 1 thiếu lịch sử. Comparison để trống
vì recipe này kiểm tra workflow. Các metric ML lịch sử có model/run/lock riêng.
Kịch bản ba role tại [demo](demo.md).

```bash
make app-stop    # dừng đúng API/worker/web do launcher tạo, giữ PostgreSQL/data
make app-start   # dùng bundle và build đã chuẩn bị
make app-status
# Build lại sau sửa frontend, dừng processes trước:
make app-stop
make app-build
make app-start
# Khi muốn dừng cả DB:
docker compose -f deploy/compose.local.yml stop
```

Launcher kiểm tra PID/start-time, giữ credentials và không ghi đè account khác
cùng tên. Bundle thay đổi protocol/runtime sẽ bị từ chối. Kiểm tra nguyên nhân
trước khi chuẩn bị output mới, không sửa hash của artifact cũ để ép load.

## Phát triển từng service hoặc dùng bundle riêng

```bash
make app-db
make app-migrate
# Bootstrap chỉ dành cho DB chưa có account, nhập password qua hidden prompt:
env -u RISKLAB_DATABASE_URL RISKLAB_DATABASE_URL_FILE=.secrets/local_api_url \
  uv run risklab-app bootstrap-admin admin.demo
# Ba terminal:
make app-api
make app-worker
make app-web
```

API và worker nhận settings qua environment, không tự đọc `.env`. Với bundle
operator chuẩn bị, đặt cùng các biến dưới đây cho cả hai process.

```bash
export RISKLAB_APP_ROOT=/absolute/demo-root
export RISKLAB_CONFIG_DIR=/absolute/demo-root/configs
export RISKLAB_STORAGE_ROOT=/absolute/demo-root/objects
export RISKLAB_CATALOG_FILE=/absolute/demo-root/catalog.json
export RISKLAB_CATALOG_SHA256=CATALOG_SHA256
export RISKLAB_RELEASE_FILE=/absolute/demo-root/release.json
export RISKLAB_MODEL_FILE=/absolute/demo-root/model.pkl
# Tùy chọn: RISKLAB_COMPARISON_FILE=/absolute/demo-root/comparison.json
```

Release pin model hash, `library_versions`, đầy đủ `protocol_hashes`, ordered
`feature_columns` và `feature_groups`. `config_dir` phải là `root/configs` đã xác
minh. Thiếu catalog khiến import unavailable. Worker thiếu scorer vẫn xử lý import,
giữ scoring ở QUEUED và không tiêu thụ attempts; sau khi sửa bundle phải restart
worker để nạp model. Schema chuẩn tại
`business/db.py`, migration hiện có `0005_worker_permissions`.

## UI và quyền

| Route | Khả năng |
|---|---|
| `/` | Overview workload trong quyền Analyst/Manager, kết nối app |
| `/applications`, `/review-queue` | Search/filter/pagination phía server, TanStack sort trên trang hiện tại. Queue mặc định Analyst ASSIGNED, Manager PENDING_DECISION |
| `/applications/[id]` | Prediction/provenance/explanation, Analyst draft/submit, Manager return/decide, history |
| `/imports` | Manager import catalog, retry giữ idempotency cho cùng upload intent |
| `/models`, `/reports` | Pinned model chỉ đọc, workload/comparison thực có. Registry mutation qua operator CLI |
| `/audit` | Admin account audit, business users đi tới history theo hồ sơ |
| `/settings`, `/admin/users` | Session/quyền hiện tại, Admin quản lý accounts |

UI dùng Tailwind v4, shadcn/ui, TanStack Table v8 và Recharts, light mode,
desktop/mobile. Conflict cần reload có xác nhận bỏ edits. Dialog giữ focus và
hỗ trợ cancel/Escape. FastAPI quyết định role/object access theo [RBAC](../product/rbac.md).

## Kiểm tra và vận hành

```bash
make test
uv run pytest tests/business tests/unit/test_business_foundation.py
cd apps/web
npm run lint && npm run typecheck && npm run format:check && npm run build
npm run test:e2e
npm run test:e2e:workflow
```

Business tests dùng DB disposable và grants thật. Docker thiếu thì PostgreSQL
checks skip và chưa có evidence DB. CI control URL chỉ được trỏ test instance
riêng tên `risklab_test_control`. E2E dùng port 13000/18000 và 13001/18001 riêng
để tránh dùng chung cửa sổ rate limit 10 login/5 phút. Dọn đúng container run ID.

Secrets local mode 0600 và DB volume phải đồng bộ, tránh `down -v`. API/worker
login tách owner/migrator. Chỉ đặt một trong `RISKLAB_DATABASE_URL` hoặc `_FILE`.
CSRF token ở memory browser, mutation no-store/same-origin. Build artifacts,
credentials, data và traces được git-ignore. Remote release, secrets rotation và
backup/restore theo [deployment](../operations/deployment.md) và
[backup](../operations/backup-restore.md).
