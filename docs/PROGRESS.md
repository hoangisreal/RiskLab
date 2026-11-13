# Trạng thái RiskLab

## Hiện tại — 2026-11-13

Ứng dụng chạy **local bằng synthetic data** với Next.js, FastAPI, PostgreSQL và
Python worker. Tám màn UI theo yêu cầu có navigation, bảng search/filter/sort,
loading/empty/error, validation, confirmation và desktop/mobile behavior.
Analyst đề xuất; Manager chốt quyết định mô phỏng; Admin quản lý tài khoản.
Model prediction/provenance/explanation tách khỏi quyết định và history của người dùng.

| Thành phần | Implementation và giới hạn |
|---|---|
| Business | Import → worker scoring → assign → draft/submit → return/decision; RBAC phía server, revisions, separation of duties và audit chung transaction |
| Worker/schema | Lease heartbeat/fencing, retry budget kể cả crash; migration `0005_worker_permissions`, worker dùng DB login riêng |
| ML | Kiểm model bytes, protocol, dependencies và ordered columns/groups trước deserialize; giữ historical locks/references |
| UI | Tailwind/shadcn, TanStack Table, Recharts. Model Registry chỉ đọc, Settings/session/account theo quyền |
| Vận hành | Launcher local, preflight read-only, backup/checksum và atomic restore/recovery trên PostgreSQL disposable |

### Review độc lập và bản sửa

Muse CLI đã review source bằng `muse-wonder-1.0`, requested reasoning `xhigh`,
read-only, gồm các vòng follow-up. P2 reload lỗi được Wonder xác nhận đã đóng;
phân trang history tiếp tục sửa điểm dừng khi dedupe events. Reviewer không chạy tests; kết quả thực chạy do lượt tích hợp bên dưới.
Raw review/logs giữ local trong `reports/operations/wonder-xhigh-review/`.

- Khóa target Analyst khi assign/return để serialize với disable/đổi role.
- Worker thiếu scorer giữ scoring QUEUED, attempts không tăng; vẫn xử lý import.
  Sửa bundle xong cần restart worker. Audit import/retry ghi đúng Job ID.
- Cấu hình peer proxy tường minh cho Uvicorn, reject wildcard/all-address trust.
  Preflight đối chiếu origin/site đã render. Validation idempotency key diễn ra
  trước ghi upload.
- Detail state/ref được key theo hồ sơ/session, reload sau mutation giữ loading.
  Reload lỗi chặn toàn bộ stale controls đến khi retry thành công. History tải
  được các trang cũ; audit pagination dùng total và loading khi đổi trang.
- Launcher xác minh đủ bundle/catalog/CSV, giữ process ownership nếu stop chưa xong.
  Backup offsite lỗi trả failure và giữ bản local đã xác minh.
- Các bản sửa trước đó bảo vệ CSRF/revoked sessions, current draft, model integrity,
  alias durable intent và lease expiry. Restore + revoke sessions/reset leases chung
  một transaction; lỗi recovery rollback schema/data.

Controller Wonder/Spark/Sol cùng scripts/tests/guide riêng được bỏ khỏi repo theo
owner. Muse CLI tiếp tục dùng trực tiếp. Tám batch docs được gộp thành
[backlog còn mở](tasks/README.md); agent/human guides compact về nguồn chuẩn.
Presentation và CatBoost scratch local được ignore. Code giữ cấu trúc dễ đọc;
contracts và historical ML evidence giữ nguyên.

### Kiểm chứng hiện hành

| Command/check | Kết quả |
|---|---|
| Baseline `make test` trước Wonder fixes | 694 passed / 10 data deselected, 226.29s |
| Focused business/lifecycle/preflight | 173 passed, 68.52s; PostgreSQL thật, cả hai chiều race assign/return vs disable |
| Full `make test` sau dọn controller | **681 passed / 10 data deselected**, 244.57s; Ruff/format/mypy pass, PostgreSQL/Docker không skip. Một Starlette/httpx deprecation warning |
| Frontend lint/typecheck/format | Pass |
| PostgreSQL workflow, thêm audit retry HTTP | 31 passed, 31.59s |
| Browser identity/workflow | 10 passed (27.3s) + 4 passed (41.4s), desktop/mobile; failed reload, history load-more với duplicate IDs và audit pagination |
| Production build + local restart | Pass; API/worker/web running; Chromium smoke cả ba role, 16 route visits, 0 page errors |
| Docs relative links / `git diff --check` | Pass, 182 relative links. Docs Markdown 38 → 30 file, giảm khoảng 21% số dòng so với base git |

Tests dùng synthetic/disposable data, không mở Test partition thật, đổi model
protocol sources hoặc tái gán ML evidence. Số tests giảm sau khi bỏ controller;
regression tests của ứng dụng được giữ và bổ sung.

### Local và phạm vi còn mở

`make app-local` chuẩn bị PostgreSQL `127.0.0.1:15432`, API `127.0.0.1:8000`,
worker và standalone web **http://localhost:3000**. Bundle `synthetic-local-v1`
và CSV ở `data/application/local-demo/`; credentials ngẫu nhiên ba role ở
`.secrets/local_demo_accounts.json` (0600, git-ignored). Smoke local có
3 applications, 2 scored, 1 INCOMPLETE, Analyst submit và Manager quyết định;
còn hồ sơ ASSIGNED để demo. Commands tại [application](development/application.md).

B06-T03/B07-T02/T03 còn **BLOCKED ở phần vận hành đích**. `deploy.sh --execute`
dừng rõ ràng. Chưa có evidence VPS/domain/TLS, publish registry, image/schema/commit
thực chạy, off-VPS/key recovery, RPO/RTO hoặc remote rollback drill. Backup cần
operator maintenance/drain. Restore cần target cách ly và một operator tại một
thời điểm. Audit alias hỏng dừng fail-closed, phục hồi cần đối chiếu registry.
[Backlog](tasks/README.md) giữ điều kiện nghiệm thu và quyền cần thiết.

## Historical ML baseline (recorded 2026-11-08)

Current status of RiskLab. Accepted results and evidence are in
`docs/RESULTS.md`; design decisions are in `docs/DECISIONS.md`; architecture
is in `docs/PROJECT.md`.

- ML baseline last updated: 2026-11-08 (not re-verified by the MVP documentation update).
- The previous ML-only scope was recorded complete: data validation and partitioning, the
  contract-1.1 experiment protocol on Selection, exact reproduction, API
  serving, monitoring, rollback and load evidence, and the final locked Test
  evaluation (run `50d36f3b142a41039ff4d72967242f9a`, lock
  `17b83143e33bb907`).
- Test partition openings: 3 in total, one per evaluated lock (legacy
  `e98453beb10d833e`; contract-1.1 `402b9e050159762d` and
  `17b83143e33bb907`, both evaluating the identical artifact). The append-only
  `reports/final_eval/ledger.jsonl` preserves both. The contract-1.0 result
  is historical only and does not evaluate the corrected contract.

## Historical ML milestones

| Milestone | Status | Notes |
|---|---|---|
| Foundations | Done | Fresh-copy setup and CI pass on synthetic data |
| Model comparison (E1) | Done | LightGBM selected over Logistic Regression |
| Experiment protocol (E2–E4) | Done | Source selection, calibration and threshold frozen on Selection |
| Locked Test evaluation | Done | Metrics, confidence intervals and subgroups published in the model card |
| Serving | Done | Registered bundle, 500-payload API parity check, audited rollback |
| Monitoring | Done | 100 calibration and 100 held-out control batches; known limits disclosed |

## Current frozen artifact

LightGBM, no calibration, threshold `0.08040201`, artifact SHA256
`f5e4d127174bde337f03ed14d47ba050efe6f45ba4c599d0e6e558a256b0d676`, served as
registry v7 under lock `17b83143e33bb907`. Details: `docs/RESULTS.md` §4–§5
and `docs/model_card.md`.

## ML baseline notes and current boundaries

- The recorded policy analysis is complete: `CODE_GENDER` stays
  excluded; including it would require an explicit policy decision
  (`docs/DECISIONS.md` D15).
- Any future Test opening requires explicit authorization and a recorded
  reuse acknowledgment before the read (D18).
- Hosted VPS deployment, account authentication/RBAC and audit are now required
  for the MVP (D22–D25). Managed tracking storage remains optional; these scope
  decisions do not mean the corresponding application features have shipped.

## Follow-up investigations (2026-11-04)

- Mild-shift sensitivity: probed under D19. Alert scale 0.5 qualifies (run
  `f0dafdc48e374218a9267492c291d786`) and detects the mild shift (run
  `cfbe4b583c9b4ee19fb790f2ac657c66`), but it is **not adopted**: adopting it
  needs a full protocol rerun, a new lock and registration, and would detach
  serving from the Test evidence of lock `402b9e050159762d`.
- Latency: profiled in `docs/OPERATIONS.md` §5. The bottleneck is
  per-request DuckDB SQL planning (about 62 ms of about 79 ms
  `prepare_input`); SQL texts are cached per process. The indicative 100 ms
  concurrency target remains unmet and disclosed; no SLO is claimed.
- Subgroups: the Selection replication in `docs/RESULTS.md` §5 shows the age
  FPR/FNR pattern is a base-rate and global-threshold trade-off, not
  per-band mis-calibration. No threshold change was made; group-specific
  thresholds would be a policy and legal decision requiring a new lock.
