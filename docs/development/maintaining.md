# Đọc code và maintain RiskLab

## Bắt đầu từ đâu?

Đọc [bản đồ project](../README.md), trạng thái đầu [PROGRESS](../PROGRESS.md), rồi
contract liên quan. [Kiến trúc](../architecture/overview.md) liệt kê đúng entrypoint
đang có; [PROJECT](../PROJECT.md) mô tả ML. Các phần lịch sử không mô tả runtime mới.

Theo một workflow nghiệp vụ: `business/features.py` → PostgreSQL + audit →
`business/jobs.py` → `business/worker.py` → `business/scoring.py`. Theo scoring cũ:
`api/main.py` → `api/service.py` → shared features/SQL → model/explanation.
Frontend gọi cùng business API; không suy quyền từ nút ẩn hoặc client role.

## Lệnh kiểm tra hiện có

```bash
make test                         # Ruff, format check, mypy, synthetic pytest
uv run pytest tests/business      # PostgreSQL disposable, migrations/quyền/concurrency
```

Frontend/build/browser commands và cách khởi động nằm ở
[application guide](application.md). Chạy focused tests trước; sau sửa liên quan
nhiều phần mới chạy full gate. PostgreSQL bị skip không tính là đã kiểm chứng.
Dùng Python 3.12 của project qua `uv run`, không suy runtime ML từ Python hệ thống.
Không đổi dependencies để che warning hoặc chỉnh tolerance để che regression.

## Cách sửa và review

- Đọc source và tests; tái hiện trigger, xác định invariant trước khi sửa.
- Quyền/state/concurrency cần negative tests và rollback evidence. Migration cần
  fresh-install/upgrade trên PostgreSQL thật; không thay bằng SQLite.
- Artifact loading cần kiểm tra đúng bytes/runtime; metadata hoặc model catalogue
  không phải execution evidence. [ML compatibility](ml-compatibility.md) là checklist chuẩn.
- Comment tiếng Anh giải thích invariant, missing semantics, transaction/retry và
  compatibility; không nhắc lại cú pháp. Hướng dẫn người đọc bằng tiếng Việt.
- Kiểm tra diff và relative links. Báo command thực chạy, result, skips và giới hạn;
  không cập nhật AC từ test chưa chạy. Kết quả mới vào [PROGRESS](../PROGRESS.md),
  quyết định mới bổ sung [DECISIONS](../DECISIONS.md), không sửa lịch sử ML.

## Cộng tác với coding agent

[AGENTS.md](../../AGENTS.md) quy định scope và authorization. Task giới hạn file,
contract/AC, dependencies và checks theo [handoff](../tasks/handoff-template.md).
Khi được yêu cầu reviewer, đưa diff và execution evidence; không đưa secrets hoặc raw rows.
Không tạo tracker, memory hoặc journal mới để thay thế tài liệu chuẩn.
