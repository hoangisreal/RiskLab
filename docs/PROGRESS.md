# Project status

## Phạm vi MVP hiện tại (2026-11-12)

**MVP hoàn tất ở phạm vi local; chưa nghiệm thu deploy VPS.** B00–B06 DONE
(local), B07-T01 DONE (local), B07-T02/T03 BLOCKED do thiếu VPS/domain,
deployment approval, registry authorization và off-VPS backup/key channel.
Không tự deploy hoặc tạo hạ tầng có phí khi chưa được owner ủy quyền.

| Hạng mục | Trạng thái | Nguồn chuẩn |
|---|---|---|
| Phạm vi MVP, actors, non-goals | Đã đặc tả | [PRD](product/prd.md) |
| RBAC, separation of duties | Đã chạy local gồm hồ sơ/decision | [RBAC](product/rbac.md) |
| Import/scoring/review state machines | Đã triển khai + test PostgreSQL/Playwright | [Workflow](product/workflow.md) |
| Next.js/FastAPI/PostgreSQL/worker | Đã chạy local end-to-end | [Architecture](architecture/overview.md) |
| VPS/Compose/Caddy, secrets, CI/CD | Tooling local DONE, chưa deploy | [Deployment](operations/deployment.md) |
| Backup/restore | Script + rehearsal local DONE, chưa có off-VPS drill | [Backup/restore](operations/backup-restore.md) |
| AC-01–AC-21 | Ma trận bên dưới; AC-17–AC-19 BLOCKED | [Acceptance](product/acceptance.md) |

Advanced UI, SSO, Kubernetes/Terraform và microservices ngoài phạm vi.
Agent: [AGENTS.md](../AGENTS.md). Backlog: [8 batch / 30 task](tasks/README.md).

## B03–B07 — implementation và kiểm chứng local (2026-11-12)

### Đã triển khai

- Migration `0004_workflow`: `jobs`, `predictions`, `reviews`, `decisions`,
  idempotency cho import/decision; runtime không UPDATE/DELETE prediction,
  decision hoặc audit. `SCHEMA_REVISION` khớp head mới.
- PostgreSQL job queue (`business/jobs.py`): claim `SKIP LOCKED`, lease 120s,
  heartbeat, attempt fencing, tối đa 3 attempts với backoff, partial unique
  index chống hai job active trùng. Worker `risklab-worker` xử lý import và
  scoring ngoài transaction, commit prediction + job + audit nguyên tử.
- ML adapter (`business/scoring.py`): verify SHA256/contract/groups/thứ tự cột
  của bundle operator ghim rồi dùng lại shared serving/explanation; không sửa
  file thuộc protocol hash, không suy human decision từ model output.
- API/UI workflow: import idempotent, list/detail/snapshot/prediction/history,
  assignment, scoring retry, review draft/submit/return, decision với
  separation of duties (kể cả draft reviewer sau đổi role), workload/model
  reports. Trang `/imports`, `/applications`, `/applications/[id]`, `/reports`.
- Delivery tooling: production Dockerfile targets, `deploy/compose.prod.yml`,
  Caddy (chỉ 80/443 public, chặn legacy routes), CI PostgreSQL/frontend/E2E,
  deploy dry-run/preflight, backup/restore scripts + release/comparison mẫu.

### Actual checks (2026-11-12)

| Command/check | Kết quả |
|---|---|
| `make test` | Ruff/format/mypy pass; **562 passed / 10 data deselected**, 240.90s; 1 Starlette/httpx warning sẵn có |
| `uv run pytest tests/business tests/unit/test_business_foundation.py tests/unit/test_import_csv.py tests/unit/test_demo_catalog.py` | **151 passed** (PostgreSQL thật: imports, identity, workflow, fencing, separation, concurrency, reports, parity worker-vs-legacy) |
| Frontend lint/typecheck/format/build | Pass (`npm run lint`, `typecheck`, `format:check`, `build`) |
| `npm run test:e2e` | **10 passed** (foundation + identity, desktop/mobile) |
| `npm run test:e2e:workflow` | **4 passed** (import→score→assign→review→decide + Admin denied, desktop/mobile; backend riêng vì login rate limit) |
| Production image smoke | `docker build --target business` và web image build thành công; `docker compose -f deploy/compose.prod.yml config` hợp lệ; `scripts/deploy.sh --dry-run` pass |
| Backup/restore rehearsal local | Backup PostgreSQL disposable → restore DB mới: user khôi phục, sessions = 0, object khớp checksum |
| Protocol surface | Không sửa `configs/`, `sql/` hay 21 file Python thuộc protocol hash; `git diff --check` pass |

### Ma trận nghiệm thu B07-T01 (local, 2026-11-12)

| AC | Trạng thái local | Bằng chứng chính |
|---|---|---|
| AC-01 | Đạt local | Playwright workflow desktop/mobile |
| AC-02 | Đạt local | Workflow API tests: Analyst/Admin decide 403, DB không có decision |
| AC-03 | Đạt local | Cross-object 404/403 cho detail/snapshot/prediction/history/list |
| AC-04 | Đạt local | Identity tests + Admin business denied (API + E2E) |
| AC-05 | Đạt local | Role-switch separation test (`SEPARATION_OF_DUTIES`) |
| AC-06 | Đạt local | Identity session/revocation/concurrency tests |
| AC-07 | Đạt local | State/revision/reason/prediction guard tests |
| AC-08 | Đạt local | Hai Manager race: 1 decision, 201/409; idempotent replay |
| AC-09 | Đạt local | INCOMPLETE chặn scoring; missing ≠ no-history (import/workflow tests) |
| AC-10 | Đạt local | Prediction provenance pin input/model/run/lock/policy; history giữ model cũ |
| AC-11 | Đạt local | Audit cùng transaction; runtime no UPDATE/DELETE audit/prediction/decision |
| AC-12 | Đạt local | Duplicate/different-payload imports, lease reclaim, stale completion, rollback biên |
| AC-13 | Đạt (reproduction 2026-11-12) | `scripts/reproduce.py` verify 9/9 reference ok, candidate verified, không mở Test |
| AC-14 | Đạt local (synthetic) | Leakage/parity suite + worker-vs-legacy parity test; Test không mở |
| AC-15 | Đạt local | Fresh/upgrade migration + constraints trên PostgreSQL thật |
| AC-16 | Đạt local | E2E 3 role, states, bàn phím/form, responsive |
| AC-17 | **BLOCKED** | Cần VPS/domain/TLS thật (B07-T02) |
| AC-18 | **BLOCKED** | Rehearsal local xong; cần off-VPS backup + key channel thật |
| AC-19 | **BLOCKED** | Tooling xong; cần registry/GitHub environment/deploy thật |
| AC-20 | Đạt local | UI/docs ghi simulation, TARGET và log-odds limits |
| AC-21 | Một phần | Docs/demo script + fresh local walkthrough xong; video/publish chờ B07-T02 |

### B05-T02 — reproduction (2026-11-12)

Chạy `MLFLOW_TRACKING_URI=http://127.0.0.1:5055 uv run python
scripts/reproduce.py --out /tmp/risklab-reproduce-b05` trên dữ liệu local:
`Verify passed` cho cả 9 file reference (e1, e1_served, e2, e3, e4, ingest,
partition, policy, validate; không có differences), `Candidate verified
(500 applicants)`, kết luận của script: không đánh giá Test, không đổi
registry alias. Output nằm ngoài git tại `/tmp/risklab-reproduce-b05`;
server MLflow tạm đã tắt sau khi chạy. Comparison report cho API lấy từ
Selection đã ghi tại [RESULTS.md](RESULTS.md) qua file operator
`deploy/comparison.example.json`, không tính lại metric khi GET.

## B01/B02 — implementation và kiểm chứng local (2026-11-11)

### Đã triển khai

- `src/risklab/business/`: entrypoint riêng, environment/secret-file settings,
  standardized errors, body caps, no-store responses và JSON request metadata logs.
  Không mount legacy `/predict`/monitoring/admin-model routes vào business API.
- PostgreSQL 15 + SQLAlchemy/Alembic, single head `0002_login_limits`, separate
  migrator/runtime/worker groups; runtime không DDL hoặc update/delete audit.
  Audit writer dùng transaction caller; filesystem storage content-addressed,
  immutable, checksum/no-follow checks, không public static snapshots.
- Argon2id password hashing, digest-only server sessions, CSRF/Origin, secure
  production cookie, session rotation/logout/expiry/revocation. Login limit chung
  PostgreSQL 10 attempts/5min theo IP/username; không dùng Redis.
- Admin create/list/change/disable/reset/audit; last-active-Admin concurrency guard,
  self-role-change guard, expected revision và session revocation. Active Analyst
  lookup cho Manager và object-access helper; chưa có application routes để coi
  toàn bộ AC-03/04 đã được chứng minh.
- Next.js 16.4.6/React 19.3.1/TypeScript pinned, local shell/login/Admin UI,
  same-origin API và role-aware navigation. Client không lưu token localStorage,
  không truy DB. Desktop/mobile browser tests dùng API và disposable PostgreSQL thật.
- Local workflow: [application guide](development/application.md), `make app-db`,
  `make app-migrate`, `make app-api`, `make app-web`, operator-only bootstrap CLI.

### Actual checks

| Command/check | Kết quả |
|---|---|
| `make test` sau B01/B02 | Ruff/format/mypy pass; **455 passed / 10 data deselected**, 178.13s; 1 Starlette/httpx deprecation warning |
| `uv run pytest tests/business tests/unit/test_business_foundation.py` | **45 passed**: real PostgreSQL fresh/upgrade, ORM/migration drift, constraints/privileges, rollback audit, auth/RBAC/revocation/rate-limit/concurrent Admin/CLI bootstrap |
| Frontend lint/typecheck/format/build | Pass qua `npm run lint`, `typecheck`, `format:check`, `build` |
| `npm run test:e2e` | **10 passed**, lần xác minh cuối 12.4s; desktop/mobile, Admin create/change/disable, session revoke (kể cả logout từ shell còn mở), Analyst/Manager direct API 403, connection failure/retry, keyboard/no horizontal page overflow |
| `npm audit` và `npm audit --omit=dev` | **0 vulnerabilities**; giữ ESLint 9 để tương thích Next plugins (deprecation warning được công bố) |
| `make app-db app-migrate` + runtime probe | Local Compose PostgreSQL healthy trên 127.0.0.1:15432; actual user `risklab_api_login`, schema ready, audit UPDATE denied |
| Browser screenshots | Đã xem desktop foundation và mobile Admin; sửa table thành mobile labelled rows, search/pagination wrapping và refresh history sau mutation |
| Protocol/dependency check | 58 protocol entries và 122 original source/config/SQL/reference files không đổi so với HEAD; toàn bộ 126 locked package versions cũ được giữ, chỉ bổ sung app dependencies |
| Config/bundle smoke | Invalid Next proxy origin bị từ chối không echo giá trị; static browser bundle không chứa PostgreSQL URL scheme, owner-secret filename hoặc synthetic browser-test password; đây là marker smoke, không phải security audit toàn diện |

Self-review đã xử lý migration-index/ORM name drift, CLI single-command flattening,
Next effect-state lint, ambiguous browser locators, stale account-audit responses,
CSRF/session secrets logging boundary và test-container teardown. Local startup
phát hiện container UID không đọc được mode-0600 host secret; đã chuyển provisioning
API/worker logins sang host script, **không** nới permissions hoặc xóa DB volume.

Npm advisory GHSA-g3hv-75wg-m249 được xử lý bằng scoped minimatch override; đã chạy
lại lint/build/E2E/audit. Không thay các ML dependency versions sẵn có, không thay
58 protocol-hashed entries, không mở Test thật. B07 mới audit/review nghiệm thu toàn
bộ sản phẩm; các checks ở đây không phải public deployment/restore evidence.

### B03 đang làm

Đã bổ sung pure `import_csv.py` với fixture validation, byte/row/error caps,
canonical replacement UUID, safe lookup IDs và exact-file fingerprint. Bộ parser
có 45 cases (gồm CSV writer roundtrip qua 24 tổ hợp size/quoting/newline/BOM).
Focused command `uv run pytest tests/unit/test_import_csv.py tests/business
tests/unit/test_business_foundation.py`: **90 passed**; warning parametrize iterator
được sửa bằng materialized list. Full `make test` sau parser: Ruff/format/mypy
pass (82 source files), **500 passed / 10 data deselected**, 174.91s; chỉ còn
Starlette/httpx warning sẵn có. Không có real-data/Test opening.

Chưa có application/import persistence hoặc worker/API orchestration; B03-T01
vẫn IN_PROGRESS, không đánh dấu DONE từ parser-only checks.

## Kết quả B00 — baseline và contracts

### Môi trường và kiểm kê

- Source baseline HEAD: `0dc871d` (`docs: record E5 challenger study outcome`).
  Worktree dirty từ docs/tests; không tự commit hoặc tạo selection lock mới.
- Linux x86_64, `uv 0.12.21`, project interpreter `uv run --locked python` =
  **3.12.14**, đúng dải `>=3.12,<3.13`. Interpreter `python3` hệ thống không phải
  runtime project; các checks bên dưới dùng `uv run`.
- Inventory/AST: 67 tracked Python source files, 12 tracked scripts và 58 tracked
  test files; B00 thêm 2 integration test files và fixtures synthetic. Danh sách
  consumers/boundaries: [ML compatibility checklist](development/ml-compatibility.md).
- Không có `apps/web/`, `migrations/` hoặc `alembic.ini`; CI hiện chỉ có Python gate.
  Docker/Compose đang là local ML stack, chưa phải VPS web application.
- Các thư mục untracked sẵn có `presentation/`, `catboost_info/` được giữ nguyên.
  Không đọc `.env`, datasets thật hoặc model artifact thật để kiểm kê source.

Runtime package versions được đọc từ metadata, không nâng hoặc đổi lockfile:

| Package | Version | Package | Version |
|---|---|---|---|
| duckdb | 2.0.0 | fastapi | 0.162.0 |
| lightgbm | 4.7.0 | mlflow | 3.17.2 |
| numpy | 2.5.4 | pandas | 3.1.0 |
| pyarrow | 25.0.1 | pydantic | 2.14.2 |
| scikit-learn | 1.9.1 | pytest | 9.2.0 |
| ruff | 0.17.3 | mypy | 2.4.1 |

### Checks đã chạy

| Check | Kết quả thực tế |
|---|---|
| Initial `make test`, tool timeout 120s (2026-11-10) | Ruff/format/mypy pass; pytest bị SIGTERM 143 do hết thời gian tool, chưa có assertion failure trước khi dừng; không tính là suite pass |
| Original baseline selection, 2026-11-10 | 383 passed / 24 deselected, 151.81s; loại 10 real-data cases và 14 B00 cases trong tracked tests, ignore 2 new files |
| Focused 6-file command trong [development guide](development/maintaining.md), 2026-11-10 | 71 passed, 15.14s |
| `make test` sau thêm B00 tests, 2026-11-10 | Ruff/format/mypy pass; 410 passed / 10 data deselected, 161.97s |
| `make test` xác minh lại sau rà soát, 2026-11-11 | Ruff check/format pass (179 files); mypy pass (67 source files); **410 passed / 10 data deselected**, 162.97s |
| Byte/hash check against HEAD, 2026-11-11 | 124 source/config/SQL/reference/dependency files không đổi; 58 protocol entries không đổi (28 YAML, 9 SQL, 21 Python) |
| Docs/contract check, 2026-11-11 | `git diff --check` pass; 217 local links resolve, 10 JSON examples và fixture JSON parse; Python AST parse; 30 task dependencies không vòng lặp, 3 DONE/27 TODO |

Baseline-selection command (chạy trước full B00 gate; không sửa marker/tolerance):

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MLFLOW_DISABLE_AGENT_HINT=1 uv run pytest \
  -m 'not data' \
  --ignore=tests/integration/test_business_contract_fixtures.py \
  --ignore=tests/integration/test_artifact_compatibility.py \
  --deselect=tests/unit/test_cli.py::test_operational_command_help_preserves_flags \
  --deselect='tests/unit/test_lock_integrity.py::test_changed_lock_context_is_rejected[commit]' \
  --deselect=tests/unit/test_lock_integrity.py::test_new_business_yaml_inside_configs_changes_protocol_but_docs_do_not \
  --deselect=tests/unit/test_serving_safety.py::test_protocol_mismatch_is_rejected_before_artifact_loading \
  --deselect=tests/integration/test_api_synthetic.py::test_request_keeps_model_snapshot_during_concurrent_reload \
  --deselect=tests/integration/test_api_synthetic.py::test_legacy_prediction_wire_contract_and_threshold_boundary
```

Suite có 1 warning sẵn có: Starlette deprecates httpx TestClient integration.
Không làm dependency upgrade trong B00 để che warning. Không có test failure
được phát hiện trong các lần chạy hoàn tất; không suy ra không còn bugs khác.

### Deliverables và giới hạn

- [Business API v1 specification](business_api_contract.md): routes/DTO/examples,
  pagination/error semantics, revisions, sessions/CSRF và idempotency.
- [Demo import contract](demo_import_contract.md): versioned trusted synthetic
  catalog + CSV, bounds, field mapping và safe failure/report semantics.
- [Synthetic fixtures](../tests/fixtures/business_v1/README.md) được kiểm chứng
  bằng legacy raw schema, relational validation và shared feature SQL. CSV fixture
  intent checks **chưa** là production importer/DB/API acceptance tests.
- 27 test cases bổ sung cho fixtures, operational CLI help, lock/config identity,
  incompatible bundle reject, fresh-process artifact loading, threshold/wire
  contract và deterministic reload interleaving. Không sửa production sources.
- [Compatibility checklist](development/ml-compatibility.md) ghi dependency ngược,
  entrypoint consumers, serialization identity và migration guardrails.
- Chưa chạy real-data tests, full training/reproduction, real registry operations,
  Docker build, hosted CI hoặc deploy. Không mở Test Home Credit thật. Synthetic
  test models/temp final-eval fixtures không phải published model evaluation.
- B01 có thể bắt đầu ngoài protocol surface cũ. Trước B03 protocol/module moves,
  cần bundle-specific migration/compatibility evidence; clean-tree commit/re-lock,
  alias changes và Test opening cần authorization tương ứng. Không có blocker cho
  foundation B01, nhưng các giới hạn này vẫn bắt buộc cho release.

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
