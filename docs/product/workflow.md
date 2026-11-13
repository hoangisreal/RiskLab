# State machine và invariants

**Trạng thái:** contract hành vi cho workflow đã triển khai local. Bằng chứng
kiểm chứng và các giới hạn nằm tại [PROGRESS](../PROGRESS.md).
Quyền actor theo [RBAC](rbac.md); không suy ra quyền chỉ từ state.

## 1. Import

```text
QUEUED → RUNNING → SUCCEEDED
                → FAILED
FAILED → QUEUED  (Manager retry có giới hạn, giữ attempt history)
```

- Validate file/schema và giới hạn trước khi commit hồ sơ. Import lỗi không để
  lại một phần hồ sơ; report lỗi chỉ hiển thị cho Manager có quyền.
- Với file hợp lệ, tạo hồ sơ, jobs scoring đủ điều kiện và audit trong một
  transaction ngắn sau khi validate. Không giữ transaction trong lúc đọc file.
- Idempotency key scope theo actor + operation, gắn payload hash: cùng key/cùng
  payload trả kết quả/job cũ; cùng key/khác payload trả 409. Hai request đồng thời
  vẫn chỉ tạo một import. Không coi hai key khác nhau là cùng một intent.
- Mỗi dòng có source identity trong import để worker retry không tạo hồ sơ trùng.
- Thiếu nguồn bắt buộc nhưng schema hợp lệ có thể tạo hồ sơ `INCOMPLETE`; đây
  không phải CSV parse error và không được tự chuyển sang scoring.

## 2. Feature availability và scoring

```text
Feature availability: INCOMPLETE | INVALID | READY

Scoring:
NOT_REQUESTED → QUEUED → RUNNING → SUCCEEDED
                               → FAILED
FAILED → QUEUED  (retry có giới hạn, cùng input/model identity)
```

- Chỉ `READY` mới queue scoring. Nguồn chưa cung cấp ≠ đã xác nhận không có lịch sử.
- Không có manual override để bỏ qua validation. MVP không sửa dữ liệu tài chính
  tại chỗ: import hồ sơ thay thế có `supersedes_application_id` khi cần sửa sai.
- Job pin input checksum/revision, feature contract và model bundle khi được tạo;
  thay model release không làm retry âm thầm chuyển sang model khác.
- Worker claim job trong transaction ngắn có lease/attempt token, sau đó tính
  features/inference ngoài transaction. Worker chết thì lease được thu hồi với
  retry có giới hạn. Kết quả của worker có lease cũ bị từ chối.
- Commit prediction, job success và audit atomically. Ràng buộc unique theo
  scoring operation ngăn duplicate side effects sau crash/retry.
- Không đổi `FAILED` thành risk score mặc định. Lỗi hiển thị mã lỗi an toàn;
  stack trace chi tiết không đưa cho người dùng.

## 3. Review

```text
UNASSIGNED ── Manager assign ──→ ASSIGNED
ASSIGNED ── Analyst start ─────→ IN_REVIEW
IN_REVIEW ── Analyst submit ───→ PENDING_DECISION
PENDING_DECISION ── Manager return + reason ──→ IN_REVIEW
PENDING_DECISION ── Manager decide + reason ──→ DECIDED
```

| Transition | Actor và guards | Side effects |
|---|---|---|
| Assign | Manager, Analyst nhận việc đang active | Lưu assignee, assignment audit |
| Start | Analyst được giao, prediction thành công đúng input | Lưu reviewer identity, tạo draft |
| Submit | Analyst được giao, có nhận xét và đề xuất, prediction hợp lệ | Đóng băng review revision, lưu prediction ID |
| Return | Manager, submission hiện hành, lý do bắt buộc | Giữ bản submit cũ, cho phép draft revision mới |
| Decide | Manager, submission hiện hành, chưa có decision, không từng review hồ sơ | Tạo decision bất biến và audit |
| Reassign | Manager, chưa `DECIDED`, Analyst mới active, lý do bắt buộc | Về `ASSIGNED`, giữ lịch sử; submission cũ không còn pending |

Nếu assignee không còn active/Analyst, return review không thể đưa cho người đó
xử lý; Manager phải reassign trước. Disable/đổi role không xóa lịch sử review.

### Nội dung review và quyết định

- Analyst đề xuất `APPROVE` hoặc `DECLINE` và ghi lý do; chưa phải quyết định.
- Manager chốt `APPROVED_SIMULATED` hoặc `DECLINED_SIMULATED`, bắt buộc lý do.
- Manager có thể khác đề xuất Analyst; không được sửa đề xuất để làm chúng khớp.
- UI luôn ghi rõ mô phỏng. Model `approve/review` trong contract cũ không được
  dùng để tạo human decision hoặc tự chuyển state.
- `DECIDED` là terminal trong MVP: không reopen, edit hoặc delete decision.

## 4. Consistency và concurrency

- Application có optimistic revision. Mutation gửi expected revision; server
  kiểm tra lại state, quyền và revision trong transaction. Stale request trả 409.
- Reassign/submit/decide cạnh tranh không được cùng thành công từ một revision.
- Unique decision theo application là lớp bảo vệ DB bổ sung cho state guard.
- Decision tham chiếu review revision, prediction ID và actor; prediction chứa
  input checksum, model version/hash, run/lock ID, feature contract, score,
  threshold/policy identity, warnings, explanation, timestamp và initiator.
- Không ghi đè lịch sử khi champion đổi. Không rescore hồ sơ đang review để thay
  prediction mà Analyst đã xem. Rescoring/reopening là ngoài MVP.
- Ghi chú draft có thể sửa khi `IN_REVIEW`, nhưng thay đổi cần audit; review đã
  submit bất biến. Bản mới sau return không sửa trực tiếp bản cũ.
- Retry request chốt đã thành công với cùng idempotency key trả decision cũ,
  sau khi kiểm tra quyền hiện tại; request mới hoặc revision cũ trả conflict.

## 5. Ví dụ bắt buộc kiểm thử

1. Analyst gọi trực tiếp decide endpoint: bị từ chối, không mutation nghiệp vụ.
2. Analyst A đổi ID sang hồ sơ Analyst B: không đọc được cả snapshot/explanation.
3. Người đã review đổi role thành Manager: vẫn không được chốt hồ sơ đó.
4. Hai Manager chốt đồng thời: một decision; request còn lại conflict.
5. Manager reassign trong khi Analyst submit: một transition thắng, không mất history.
6. Worker chết sau tính score/trước commit hoặc sau commit/trước acknowledgment:
   retry không tạo prediction/hồ sơ trùng.
7. Thiếu historical source: chặn scoring; xác nhận no-history dùng semantics hiện có.
8. Return rồi submit lại: decision phải trỏ bản submit mới, không trỏ bản cũ.

Đối chiếu [acceptance criteria](acceptance.md) khi triển khai tests.
