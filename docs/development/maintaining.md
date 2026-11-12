# Đọc code và maintain RiskLab

## Bắt đầu từ đâu?

1. Đọc [PRD](../product/prd.md) để biết phạm vi MVP, rồi [RBAC](../product/rbac.md)
   và [workflow](../product/workflow.md) trước khi thêm nghiệp vụ.
2. Đọc [PROJECT.md](../PROJECT.md) để xem module ML thực sự đang có;
   [architecture mục tiêu](../architecture/overview.md) không phải cây code đã triển khai.
3. Theo một request hiện tại: `api/main.py` → `api/service.py` →
   `features/online.py` → shared SQL → model → `models/explain.py`.
4. Theo một experiment: `cli.py` → `experiments/` → `models/`; đọc tests trước
   khi sửa để hiểu partition, leakage, artifact và parity invariants.

## Lệnh kiểm tra hiện có

```bash
make setup       # uv sync --locked
make test        # ruff + format check + mypy + pytest (không data marker)
```

Chạy focused test khi phát triển, ví dụ:

```bash
uv run pytest tests/integration/test_api_synthetic.py
uv run pytest tests/integration/test_parity_complete.py
```

Các lệnh tạo/mutate dữ liệu, training, registry và final-eval có giới hạn riêng
trong [OPERATIONS.md](../OPERATIONS.md). Các commands Next.js/PostgreSQL và
bootstrap Admin đã có tại [application guide](application.md); không dùng local
ML Compose như public authenticated application.

### Baseline và checks B00

Project chạy **Python 3.12.14 qua `uv run`** trong lần khảo sát; `python3` hệ thống
không phải interpreter được khóa cho project. Không dùng phiên bản interpreter
hệ thống để kết luận model/runtime compatibility. Giữ `uv.lock`, không nâng package
chỉ để hết warning. Full synthetic suite mất hơn 120 giây tại môi trường này;
tool timeout không phải test assertion failure.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MLFLOW_DISABLE_AGENT_HINT=1 uv run pytest \
  tests/integration/test_business_contract_fixtures.py \
  tests/integration/test_artifact_compatibility.py \
  tests/integration/test_api_synthetic.py \
  tests/unit/test_cli.py tests/unit/test_lock_integrity.py \
  tests/unit/test_serving_safety.py
```

Kết quả thực tế tại [PROGRESS.md](../PROGRESS.md). Đọc
[business API](../business_api_contract.md), [demo import](../demo_import_contract.md)
và [ML compatibility checklist](ml-compatibility.md) trước khi làm batch tiếp theo.
Fixture tests không có nghĩa production importer hoặc business endpoints đã chạy.

## Quy ước comment và docstring

Code/identifier/comment/docstring bằng tiếng Anh; hướng dẫn người đọc bằng tiếng Việt.

- Module docstring: trách nhiệm, giới hạn, dependency quan trọng.
- Public service: input/output, errors, side effects và transaction ownership.
- Business rule: giải thích **vì sao**, đặc biệt quyền, state và separation of duties.
- ML transform: units, null/no-history semantics, assumptions và leakage risks.
- Retry/locking: mô tả invariant đang bảo vệ; không tuyên bố exactly-once sai.
- Compatibility adapter: lý do còn giữ và điều kiện có thể bỏ.

Ví dụ comment hữu ích, không phải thay đổi source trong đợt docs:

```python
# A missing history source is not evidence of no borrowing history.
# Reject scoring instead of silently producing zero-count features.
```

Không comment từng phép gán hiển nhiên. Giải thích dài đặt ở docs và liên kết
module/tests. Comment không thay thế regression tests và phải cập nhật cùng behavior.

## Thay đổi an toàn

- Sửa field/feature: xác định consumer, contract version và migration trước;
  kiểm tra cả batch, serving và explanation, không chỉ form UI.
- Sửa module bị protocol hash: đọc `models/select.py`, giữ evidence/lock cũ;
  kể cả comment có thể đổi hash. Không sửa reference để làm test pass.
- Docs-only không đổi protocol file hashes nhưng có thể làm working tree/commit
  khác lock cũ. Không diễn giải việc này là được phép bỏ clean-tree/commit guards.
- Không move toàn bộ cây ML/SQL/configs chỉ để đồng nhất thư mục. Chia refactor
  thành behavior-preserving changes có characterization tests.
- Thay đổi API nghiệp vụ cần tests quyền trên object, không chỉ role check.
- Mỗi migration có fresh-install/upgrade tests; không dùng SQLite thay PostgreSQL
  cho kiểm thử concurrency và database constraints nghiệp vụ.

## Cộng tác với coding agent

[AGENTS.md](../../AGENTS.md) là entrypoint chung. Task handoff ghi mục tiêu,
ngoài phạm vi, file ownership, contract/AC liên quan, commands đã chạy và blockers.
Khi thực sự phân công nhiều agent: dùng branch/worktree riêng, tránh cùng sửa
lockfile/migration chain; một người chịu trách nhiệm tích hợp. Không cần task
database hoặc persistent agent memory cho MVP.
