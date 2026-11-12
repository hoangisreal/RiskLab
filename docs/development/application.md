# Chạy ứng dụng nghiệp vụ local

Đã có B01/B02 và workflow B03–B05: Manager import → worker chấm điểm →
Analyst review → Manager quyết định mô phỏng, kèm báo cáo cơ bản. Scoring API
1.1 cũ vẫn là entrypoint riêng và không được public để bỏ qua authentication.

## Yêu cầu

- Python 3.12 qua `uv`, Docker daemon, Node.js 24 (đã kiểm tra 24.22.1).
- Database local riêng trên `127.0.0.1:15432`; API `127.0.0.1:8000`; Next.js 3000.
- Đây là môi trường phát triển, không thay thế production VPS/HTTPS runbook.

## Khởi tạo

```bash
make setup
make app-db           # tạo secret files nếu chưa có, up PostgreSQL và provision local logins
make app-migrate      # dùng owner credential riêng; không chạy migration bằng API login
cd apps/web
npm ci --ignore-scripts
cd ../..
env -u RISKLAB_DATABASE_URL RISKLAB_DATABASE_URL_FILE=.secrets/local_api_url \
  uv run risklab-app bootstrap-admin admin.demo
```

Bootstrap yêu cầu nhập và xác nhận mật khẩu ẩn (12–128 ký tự), chỉ chạy khi bảng
tài khoản trống và ghi SYSTEM audit. Không có tài khoản mặc định trong ứng dụng.
Admin tiếp tục tạo Analyst/Manager bằng web; giữ mật khẩu qua kênh riêng an toàn.

Ba terminal (worker chỉ cần khi chạy import/scoring thật):

```bash
make app-api
make app-worker
make app-web
```

Worker và API cần catalog/model đã ghim của operator (browser không chọn
đường dẫn nào):

```bash
export RISKLAB_CATALOG_FILE=/path/to/catalog.json
export RISKLAB_CATALOG_SHA256=<sha256 của catalog>
export RISKLAB_RELEASE_FILE=/path/to/release.json
export RISKLAB_MODEL_FILE=/path/to/model.pkl
# Tùy chọn: RISKLAB_COMPARISON_FILE=/path/to/comparison.json
# (mẫu đã ghi bằng chứng Selection: deploy/comparison.example.json)
```

Thiếu các biến trên, import trả `503 DEPENDENCY_UNAVAILABLE` và scoring job
thất bại có thể thử lại với mã `MODEL_UNAVAILABLE`; hệ thống không tự suy
điểm số thay thế.

Mở **http://localhost:3000**, không đổi sang một origin khác khi chưa cấu hình
`RISKLAB_ORIGIN`: CSRF kiểm tra Origin chính xác. Next.js proxy `/api/v1/*` tới
backend; không có DB credentials trong frontend. Server-side authorization thuộc
FastAPI, kể cả khi mở thẳng URL Admin hoặc gọi API không qua UI.

## Secrets và local DB

- `.secrets/` không commit; scripts tạo file bằng exclusive creation, mode 0600,
  không in giá trị và không overwrite file đã có. Database URL files dành riêng
  owner/API/worker. Không copy các development secrets này sang public demo.
- `make app-db` provision lại **local** API/worker login từ secret files, không
  thao tác server khác hoặc production. Owner URL phải là database local riêng.
- Runtime login không là owner/superuser và không update/delete audit hoặc DDL.
  Login passwords được provision từ host, không nới file permissions để PostgreSQL
  container UID đọc host secrets. Compose local chỉ mount owner password khi init.
- Giữ local DB volume và secret files đồng bộ. Không tự xóa volume khi password
  không khớp; kiểm tra configuration/provisioning trước. Không dùng `down -v` để
  “sửa” môi trường có dữ liệu cần giữ.
- Dừng local DB mà giữ dữ liệu: `docker compose -f deploy/compose.local.yml stop`.
- App settings lấy từ các biến `RISKLAB_*`, không tự đọc `.env` hoặc protocol YAML.
  Chỉ đặt một trong `RISKLAB_DATABASE_URL` và `RISKLAB_DATABASE_URL_FILE`.
- `RISKLAB_ENVIRONMENT=production` yêu cầu DB URL + HTTPS `RISKLAB_ORIGIN`; cookie
  dùng `__Host-risklab_session` Secure/HttpOnly/SameSite. Local dùng cookie riêng.

## Kiểm tra

```bash
make test
uv run pytest tests/business tests/unit/test_business_foundation.py
cd apps/web
npm run lint
npm run typecheck
npm run format:check
NEXT_TELEMETRY_DISABLED=1 npm run build
npm run test:e2e
npm run test:e2e:workflow
npm audit
```

`test:e2e:workflow` dùng backend/cổng riêng (18001/13001) vì giới hạn đăng
nhập 10 lần/5 phút theo IP là hành vi thật của sản phẩm; hai suite không
dùng chung cửa sổ rate limit.

Python business tests tạo PostgreSQL container/database riêng và non-owner login,
chạy migrations thật, không dùng SQLite. Nếu không có Docker, tests này **skip**
và không được tính là đã chứng minh DB behavior. CI có thể cung cấp
`RISKLAB_TEST_DATABASE_URL` **chỉ** tới control database tên `risklab_test_control`;
fixture tạo/drop database con có prefix `risklab_test_`, cần owner test credentials.
Control phải nằm trên **PostgreSQL test instance riêng**, không cùng cluster
production: group roles là cluster-wide. Không dùng application/prod URL làm test control.

Browser tests bắt buộc Docker, chạy API với DB disposable trên port 18000 và
Next.js trên 13000. Script `browser_backend` seed credentials synthetic **chỉ
trong test DB**; không phải deploy entrypoint. Playwright kiểm tra desktop/mobile,
ba role, session revocation, direct API denial, connection failure/retry. Teardown
dọn đúng container có label run ID; không dọn các container/data khác trên máy.
Screenshots/traces nằm trong `apps/web/test-results/` và không commit.

## Security/compatibility notes

- CSRF token lưu trong bộ nhớ browser; bootstrap/reload xoay token. Client chỉ
  retry khi backend trả `CSRF_FAILED` trước mutation, không retry network error/500.
- Login bị giới hạn 10 lần/5 phút theo IP và username, lưu ở PostgreSQL để các
  API process dùng chung counters. Không trust tùy ý `X-Forwarded-For`; proxy
  forwarding/trust phải được cấu hình trong B06.
- Role change/disable/reset password thu hồi sessions; identity mutations khóa
  transaction ngắn để last-admin check không race/deadlock actor/target rows.
- Static Next.js shell không chứa user data. Data requests dùng no-store; UI
  guards không phải authorization. Error boundary không render raw exceptions.
- Next.js 16.4.6/React 19.3.1 pinned. ESLint 9.39.5 được giữ vì peer compatibility
  của Next plugins; có deprecation warning. `minimatch` >=7 trước 11.0.1 được
  override có phạm vi để xử lý GHSA-g3hv-75wg-m249; không override bản 3 có API cũ.
  Recheck lint/build/E2E/audit khi cập nhật các pins này, không chạy audit fix --force.
- Protocol-hashed ML sources/configs/SQL không đổi. Business settings/migrations
  đặt ngoài `configs/` để không invalidate bundle vì thêm app config.
