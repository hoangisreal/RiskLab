# Acceptance criteria — MVP

**Trạng thái:** checklist nghiệm thu, chưa phải báo cáo các chức năng đã pass.
Phạm vi theo [PRD](prd.md), quyền theo [RBAC](rbac.md), state theo [workflow](workflow.md).
Kết quả local và các AC vận hành còn BLOCKED nằm tại [PROGRESS](../PROGRESS.md). Bằng chứng ML lịch sử có thể tái sử dụng
chỉ khi version/protocol liên quan được xác minh, không mặc định pass runtime mới.

| ID | Điều kiện đạt | Kiểm tra/bằng chứng cần có |
|---|---|---|
| AC-01 | Manager import → Analyst review → Manager chốt qua UI | Browser E2E synthetic và kịch bản demo |
| AC-02 | Analyst gọi trực tiếp API decide bị từ chối, không mutation | API authorization test, đối chiếu DB |
| AC-03 | Analyst không đọc/ghi hồ sơ, snapshot, explanation ngoài phân công | Cross-object tests cho list/detail/download/mutation |
| AC-04 | Admin quản lý tài khoản, không có quyền đọc hồ sơ/chốt mặc định | Positive account tests và negative business tests |
| AC-05 | Người từng review không chốt cùng hồ sơ sau đổi role | Role-switch regression test, kể cả review draft |
| AC-06 | Disable/role change/reset password thu hồi session; cấm self-role-change và mất Admin cuối | Session tests và concurrent account mutations |
| AC-07 | Sai transition hoặc thiếu lý do/review/prediction bị từ chối | State-machine tests cho transition hợp lệ/bất hợp lệ |
| AC-08 | Hai request chốt chỉ tạo một decision; stale revision conflict | PostgreSQL concurrent integration test, same-key retry |
| AC-09 | Thiếu lịch sử không bị biến thành no-history; thiếu feature chặn scoring | Availability/validation tests và UI blocked state |
| AC-10 | Decision truy được review/prediction/input/model đúng lúc review | DB/FK và API provenance tests; đổi model không đổi history |
| AC-11 | Mutation và audit atomic; runtime không sửa/xóa audit | Transaction rollback tests, DB permission tests |
| AC-12 | Import/worker retry/crash không tạo side effects trùng | Duplicate concurrent imports, different payload/same key, lease recovery và crash-before/after-commit tests |
| AC-13 | Training/evaluation tái lập, báo cáo ≥ LR và LightGBM | Versioned data/config/seed/runtime manifests, commands và comparison report |
| AC-14 | Leakage và batch-serving parity được kiểm thử; không tự mở Test | Synthetic regression suite, real-data parity khi được phép; Test opening cần approval riêng |
| AC-15 | API và migrations có automated tests trên PostgreSQL | Fresh DB install, upgrade từ revision trước, constraints và failure cases |
| AC-16 | UI đủ ba role và states quan trọng | E2E happy/denied paths, loading/empty/error, keyboard/form accessibility checks |
| AC-17 | HTTPS, chỉ port cần thiết public, secrets không trong repo/image/log | VPS network/TLS checks và secret-handling review |
| AC-18 | Backup DB/artifacts khôi phục được vào môi trường sạch | Restore drill report theo runbook, RPO evidence và RTO đo được |
| AC-19 | Deploy release pin, health checks và rollback tương thích chạy được | CI run, image/model manifest, smoke/rollback exercise; không destructive DB downgrade tự động |
| AC-20 | UI/docs rõ simulation, TARGET/explanation limits và dữ liệu demo | Rendered UI review, provenance dataset synthetic/anonymized đã được duyệt |
| AC-21 | Người mới chạy demo/tests theo docs, có kịch bản/video | Fresh-environment walkthrough và demo artifact không lộ secrets/data thật |

## Quy tắc ghi nhận bằng chứng

- Mỗi AC được đánh dấu đạt phải liên kết commit/release, test hoặc run report,
  môi trường, ngày kiểm tra và giới hạn còn lại. Không tạo tên test giả làm bằng chứng.
- Existing tests là điểm bắt đầu cho AC-13/14, không thay thế tests nghiệp vụ mới:
  [leakage](../../tests/integration/test_pipelines_leakage.py),
  [parity](../../tests/integration/test_parity_complete.py),
  [API](../../tests/integration/test_api_synthetic.py),
  [lock integrity](../../tests/unit/test_lock_integrity.py).
- Current comparison evidence: [RESULTS.md](../RESULTS.md). Giữ run/lock IDs và
  metric interpretation; không suy ra model đã được nghiệm thu cho MVP mới.
- Automated tests không cần Kaggle credentials theo mặc định. Dữ liệu thật và
  Test partition có quy tắc riêng trong [OPERATIONS.md](../OPERATIONS.md).
- Không nghiệm thu deploy/backup bằng tài liệu đơn thuần hoặc chỉ `docker build`.
- Không thêm Advanced acceptance gates: interactive drift, batch product,
  Kubernetes hay high availability không phải điều kiện hoàn thành MVP.
