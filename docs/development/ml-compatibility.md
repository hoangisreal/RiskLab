# Compatibility khi sửa ML

Đọc [PROJECT](../PROJECT.md) cho module map và [OPERATIONS](../OPERATIONS.md)
cho commands. Checklist này áp dụng cho source hiện hành. Evidence được ghi riêng
ở [RESULTS](../RESULTS.md), [model card](../model_card.md) và [PROGRESS](../PROGRESS.md).

## Những bề mặt cần giữ

| Bề mặt | Consumers và invariant |
|---|---|
| `risklab.cli:app` | Console/Makefile/Docker/scripts, giữ commands/flags/exit semantics |
| `api.main:create_app`, legacy routes | Synthetic/data parity, monitoring/reload clients, wire contract 1.1 |
| `api.service.prepare_input` | Batch/parity/serving/worker dùng shared cleaning/SQL, source thiếu khác explicit no-history |
| `models.pipelines.CategoryCaster`, `_log1p_nonneg` | Fully qualified names trong serialized models, giữ import identity hoặc migration có kiểm chứng |
| Pipeline/FrozenEstimator/calibrator | Exact dependencies, wrapper loading, feature order/dtypes/probabilities |
| `business/scoring.py` | Pin bundle/input per operation, kiểm tra runtime/protocol/columns trước load |
| Registry | Evaluated bytes đúng hash, metadata/profile/parity, audited alias changes |

Dynamic CLI và serialized imports có thể không có direct caller trong source.
`scripts/api_local.py` đọc trusted artifact khi import, vì vậy không import để
kiểm kê. Legacy local loading không thay thế registry bundle verification.

## Hash, dữ liệu và evidence

`models/select.py::protocol_hashes` hash recursive YAML trong `configs/`, SQL
và 21 Python files được liệt kê tường minh. Kể cả comment/path cũng thay protocol.
Business/deploy settings đặt ngoài `configs/` để tránh thêm hash ngoài ý muốn.

`verify_lock` còn bind clean tree, commit, artifact/config/runtime và data context.
Docs/test changes vẫn thay git state/commit. Không sửa locks/references/tolerances
hoặc bỏ checks để công nhận bundle cũ trên runtime mới.

Preprocessing fit Train-only. Labels/IDs không vào features. Calibration fit trên
partition riêng, Selection quyết định family/sources/calibration/threshold. Test
chỉ mở với authorization riêng cho lần đó. Refactor dùng synthetic fixtures.

## Dependency boundaries

`features/online.py` dùng API config và history settings từ `root/configs`.
`features/parity.py` gọi `api.service.prepare_input`, monitoring dùng ModelStore.
Worker adapter dùng shared inference để giữ parity, chưa tách nguồn bị hash.
Bất kỳ lần tách nào phải đánh giá hash/serialization trước khi thay dependency.
Giữ `config_dir == root/configs` trong worker adapter để history cleaning dùng
đúng phần đã xác minh. Registry/storage/serialized models là trust boundary của operator.

## Checks tương ứng

| Thay đổi | Tests cần chạy |
|---|---|
| Commands/config/hash | `tests/unit/test_cli.py`, `test_lock_integrity.py` |
| Wire/threshold/reload snapshot | `tests/integration/test_api_synthetic.py` |
| Bundle/runtime/bytes/columns | `tests/unit/test_serving_safety.py` |
| Module moves/serialization | `tests/integration/test_artifact_compatibility.py` |
| Raw schemas/missing history | `tests/integration/test_business_contract_fixtures.py`, `tests/unit/test_demo_catalog.py` |
| Features/preprocessing | `tests/integration/test_pipelines_leakage.py`, `test_parity_complete.py` |
| Frozen artifact → registry → serve | `tests/integration/test_deployment_flow.py` |
| Worker pin/fencing/parity | `tests/business/test_workflow.py`, `test_jobs.py` |

Tests dùng model/partitions/journals synthetic. Historical artifact compatibility,
reproduction và published metrics cần evidence riêng đúng version. Dependency
updates phải giữ `uv.lock` và exact runtime metadata phù hợp với bundle.
