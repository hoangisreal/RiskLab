# B00 — Compatibility checklist trước refactor ML

Khảo sát source ngày 2026-11-11, baseline commit `0dc871d` cùng worktree docs/tests
chưa commit. Đây là boundary cho B01/B03, **không** phê duyệt re-lock, registry
promotion hoặc Test opening. Implementation map: [PROJECT.md](../PROJECT.md).
Kết quả checks thực tế: [PROGRESS.md](../PROGRESS.md).

## 1. Bản đồ code và ownership hiện có

Đã lập inventory/AST của toàn bộ 67 Python source files, 12 tracked scripts và
58 tracked test files; hai integration test files mới của B00 nằm ngoài HEAD.
Inventory không đồng nghĩa mọi thuật toán đã được chứng minh đúng hoặc security
audit đầy đủ. Phần đọc sâu tập trung vào contracts, callers và invariants của B00.

| Khu vực | Data/control flow cần giữ |
|---|---|
| `config.py`, `manifest.py`, `tracking.py`, `verify.py` | Strict YAML settings, path resolution, hashes/provenance, HTTP MLflow và aggregate references |
| `data/` | CSV ingest/typed casts → validation → deterministic partition → role/purpose-gated reads; không cho ordinary loader đọc Test |
| `features/`, `sql/` | Application cleaning, applicant-local aggregates, shared batch/online SQL, null/no-history semantics, input order/dtypes |
| `models/`, `experiments/` | Train-only fitting/CV, Calibration-only calibrators, Selection decisions, frozen candidates/locks, final-eval journal riêng |
| `registry/` | Bundle metadata/runtime/parity checks, registration, alias gates và append-only alias audit |
| `api/` | Public raw schema + relational checks → per-request model snapshot → features → score/explanation; metadata-only logs |
| `monitoring/` | Immutable reference profile, registered seeded simulations, drift/performance/alert policy; không tự đổi serving model |
| `synthetic.py`, tests | Synthetic raw tables và temp tracking registry; real-data tests mang marker riêng |
| Scripts, Docker, Makefile, CI | Consumers của console/API entrypoints, không phải business worker/deploy stack đã có |

Tại baseline B00 chưa có `apps/web`, business migrations hoặc Alembic config;
B01/B02 đã bổ sung riêng, xem [application guide](application.md). `presentation/` là
thay đổi untracked sẵn có, không được coi là Next.js application và không sửa trong B00.

## 2. Entrypoints và consumers

| Surface | Consumers quan sát được | Observable contract |
|---|---|---|
| `risklab.cli:app` | Console script, Makefile, Docker train, `scripts/run_selection.py`, `scripts/reproduce.py` | Command/group/flags, exit/error behavior, config-dir option |
| `risklab.api.main:app`, `create_app` | Docker Uvicorn, `scripts/api_local.py`, tests | Import/factory, legacy routes, readiness và 422/503 semantics |
| `/predict` | `scripts/load_test.py`, synthetic/data parity clients | Contract 1.1 wire shape, inclusive threshold→review, model snapshot |
| `/models/current`, `/admin/models/reload` | `scripts/rollback_demo.py`, load/deployment tests | Pinned identity, failed reload giữ model cũ |
| `api.service.prepare_input` | `features/parity.py`, prediction service | Cùng cleaning/SQL/order với batch; thiếu required source là error |
| `risklab.models.pipelines.CategoryCaster` | Serialized LGBM, MLflow `skops_trusted_types` trong `registry/register.py` | Fully qualified Python import identity |
| `risklab.models.pipelines._log1p_nonneg` | LR FunctionTransformer serialization | Import identity, clipping và missing-value semantics |
| sklearn Pipeline/FrozenEstimator/calibrators | Pickle/MLflow LR/LGBM artifacts | Exact runtime metadata, wrapper loading, feature names/probabilities |

CLI và serialized globals là dynamic consumers, không xóa chỉ vì không thấy direct
call. `scripts/api_local.py` đọc artifact local ngay khi import; inventory không
import script này. `ModelStore.load_local` chỉ dùng trusted local demo/test artifacts,
không thay production bundle verification.

## 3. Protocol và provenance

`models/select.py::protocol_hashes` tại baseline có **58 entries**: 28 recursive
YAML files dưới `configs/`, 9 SQL files dưới `sql/`, 21 Python files liệt kê tường
minh. So sánh với HEAD phải thực hiện bằng bytes/hashes, không mở datasets/models.

| Thay đổi | Hệ quả và cách xử lý |
|---|---|
| Thêm YAML nghiệp vụ dưới `configs/` | Đi vào legacy protocol dù model không dùng; đặt application/deploy settings ngoài cây đó |
| Comment/path đổi trong file bị hash | Compatibility change, không bỏ check vì “cosmetic-only” |
| Move custom transformer/module | Có thể hỏng hash và serialized imports; giữ old import alias khi hợp lý hoặc tạo bundle/protocol mới được kiểm chứng |
| Đổi dependencies/Python | Exact `library_versions()` có thể khác; không sửa metadata old bundle để ép load |
| Docs/tests-only hoặc commit mới | Không đổi protocol bytes nhưng dirty tree/commit identity khác lock cũ; không bỏ clean-tree/commit guard |
| Model release đổi | Pin identity per request/job, không ghi lại historical predictions |

`verify_lock` kiểm tra clean tree, commit, artifact/config/runtime/protocol và tùy
chọn partition/data hashes. Registry load với `expected_protocol` reject trước
artifact loading; bundle checks còn kiểm tra columns, runtime, parity và label/ID
exclusion. Đây không phải sandbox cho artifact không tin cậy: registry/storage và
bundle metadata là trust boundary; không cho browser upload serialized model.

## 4. Worker adapter nhỏ nhất

Dependency ngược đang có: `features/online.py` import `api.config`,
`features/parity.py` import `api.service.prepare_input`; `api/service.py` vừa build
features vừa dùng HTTP-shaped DTO/ServiceError. Monitoring còn dùng API ModelStore.

B03 bọc shared scoring trước, không copy SQL/cleaning sang worker. Chỉ tách phần
framework-neutral khi cần, map lỗi sang HTTP ở API boundary. Trước khi tách phải
xác định hash/serialization impacts; nếu cần protocol mới, giữ evidence cũ và
chuẩn bị candidate version mới qua quy trình được phép. Không đọc Test để kiểm
chứng structural refactor. Không đưa toàn bộ code sang cây mới chỉ để đổi tên.

Gọi `predict` trực tiếp không thay raw schema/relational checks của HTTP boundary:
adapter phải giữ cả validation và required-source checks. Worker pin bundle/input
identity khi enqueue và dùng lại khi retry, không resolve mutable alias mỗi lần.

Một dependency path cần lưu ý: `build_online_features` lấy settings từ
`root/configs`, trong khi caller có `config_dir` riêng. Adapter phải giữ root/config
layout nhất quán hoặc sửa qua protocol migration có parity coverage; không giả
định config-dir tùy ý đã hoạt động giống mọi path.

## 5. Coverage giữ hành vi

| Invariant | Tests |
|---|---|
| CLI operational flags | [test_cli.py](../../tests/unit/test_cli.py): safe help checks, không thực hiện jobs/alias changes |
| Legacy wire shape/threshold/request ID | [test_api_synthetic.py](../../tests/integration/test_api_synthetic.py): below/equal/above threshold và response keys |
| Reload xen giữa validation và prediction | Cùng file: deterministic interleaving, request cũ giữ identity, request sau thấy model mới; không phải stress test đa luồng |
| Commit mismatch và recursive config hashes | [test_lock_integrity.py](../../tests/unit/test_lock_integrity.py) |
| Incompatible image bị reject trước loaders | [test_serving_safety.py](../../tests/unit/test_serving_safety.py) |
| Serialized import identities và fresh process | [test_artifact_compatibility.py](../../tests/integration/test_artifact_compatibility.py): LR/LGBM × none/sigmoid/isotonic |
| Snapshot availability | [test_business_contract_fixtures.py](../../tests/integration/test_business_contract_fixtures.py): raw schema, relational validation, shared SQL |
| Leakage/parity | [test_pipelines_leakage.py](../../tests/integration/test_pipelines_leakage.py), [test_parity_complete.py](../../tests/integration/test_parity_complete.py) |
| Registration/serving không refit | [test_deployment_flow.py](../../tests/integration/test_deployment_flow.py): temp synthetic HTTP MLflow, không production aliases |

Fresh-process tests chỉ load model synthetic tạo trong test. Không chứng minh mọi
historical artifact tương thích và không reproduce published metrics; B03/B05
cần bundle-specific evidence. Test final-eval hiện có chỉ dùng synthetic temp
partitions/journals, không mở Test partition Home Credit thật.

## 6. Giới hạn và approvals

- Worktree đang dirty; real lock/final-eval không hợp lệ. Commit/clean release,
  re-lock/registration/promotion cần authorization phù hợp, không tự commit.
- Starlette cảnh báo httpx deprecation là warning hiện có; không nâng dependency
  hàng loạt ở B00 hoặc ép bundle runtime metadata.
- Docker build, hosted CI, real-data tests, reproduction thật và VPS operations
  chưa được xác minh trong batch này. Không đánh dấu AC-13/14 đạt toàn bộ từ
  synthetic suite. B00 chỉ hoàn tất nền tảng/contract gate để bắt đầu B01.
