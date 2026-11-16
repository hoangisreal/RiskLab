# Trạng thái RiskLab

## Hiện tại — 2026-11-15

Ứng dụng chạy local bằng synthetic data. Đường chính cho người mới là Docker
demo một lệnh (`make demo-up`, chỉ cần Docker + Make): PostgreSQL, `demo-init`
một lần (migration, login API/worker riêng, bundle synthetic, ba tài khoản
`demo.*`), business API, worker và Next.js tại **http://localhost:3000**.
Launcher trên host (`make app-local`) là lựa chọn cho phát triển; chi tiết tại
[application guide](development/application.md).

Workflow: Manager import → worker scoring → assign → Analyst draft/submit →
Manager chốt quyết định mô phỏng; Admin chỉ quản lý tài khoản. RBAC phía
server, separation of duties kể cả draft và sau đổi role, revision conflicts,
mutation + audit chung transaction, worker lease fencing/at-least-once.
Model prediction/provenance/explanation tách khỏi quyết định của người dùng.

Audit độc lập và hai vòng review tiếp theo đã đóng toàn bộ findings
(F1–F4, R1–R3: scorer readiness, verify bundle, URL encoding + secrets tách
volume, smoke chờ đúng batch, `demo-up` không cần Python host). Quyết định tại
[DECISIONS](DECISIONS.md) D35–D36. Ngày 2026-11-16: gọn docs theo D40–D41
(xóa archive đã thay thế, viết lại README và bản đồ docs theo tầng đọc);
không đổi code, contract hay bằng chứng ML.

### Kiểm chứng gần nhất (2026-11-15, sau nâng cấp UI/UX)

| Check | Kết quả |
|---|---|
| `make test` (Ruff/format/mypy + synthetic pytest) | **698 passed / 10 deselected**, PostgreSQL disposable thật |
| Frontend lint/typecheck/format/production build | Pass |
| Browser E2E mặc định (foundation + identity + redesign) | **12 passed** (desktop/mobile): drawer mobile + Escape, filter URL/back-nav, mở/đóng chi tiết giữ draft |
| Browser E2E workflow | **4 passed** (desktop/mobile): import → review → decision, conflict/reload/history/retry giữ nguyên |
| Visual QA (dev server + backend QA riêng) | 1440/768/390: không tràn ngang; focus rõ; tap target chính ≥ 44px mobile; 48 ảnh sau nâng cấp (3 vai trò + loading/empty/error) tại [ui-refresh-2026-11-15](development/ui-refresh-2026-11-15/README.md) |
| Docker từ volumes sạch | Smoke import → scoring pass; probe HTTP tới `DECIDED`, Analyst decide 403 |
| Fault injection Docker | Worker mất scorer → `unhealthy`; bundle thiếu model → init exit 1; worker dừng → smoke exit 1; worker không đọc được `api_url` |

Tests dùng synthetic/disposable data; không mở Test partition thật cho các
thay đổi thông thường.

### Còn mở

B06-T03 và B07-T02/T03 vẫn **BLOCKED ở môi trường đích**: chưa có VPS/domain/
TLS, registry digest, backup off-VPS/key recovery, RPO/RTO đo được hay remote
rollback drill. Docker demo không phải bằng chứng production deploy. Điều
kiện nghiệm thu và backlog tại [tasks](tasks/README.md); tiêu chí tại
[acceptance](product/acceptance.md).

## Lịch sử ML (chỉ là pointer, evidence giữ ở nguồn chuẩn)

Model đóng băng: LightGBM, threshold `0.08040201`, lock `17b83143e33bb907`,
run `50d36f3b142a41039ff4d72967242f9a`. Test đã mở **3 lần tổng cộng, mỗi lock
một lần**, có ledger công khai — không viết "chỉ mở một lần toàn dự án".
Metrics, CI, subgroup và giới hạn tại [RESULTS](RESULTS.md) và
[model card](model_card.md); lý do quyết định (gồm D15, D18, D19) tại
[DECISIONS](DECISIONS.md); module map tại [PROJECT](PROJECT.md). Mở Test lần
mới cần authorization riêng từng lần.
