# RBAC và separation of duties

**Trạng thái:** B02 implement local accounts/sessions, account RBAC và Admin UI
trong business API riêng; ma trận object-level hồ sơ/decision còn cần B03/B04.
Scoring API contract 1.1 cũ không được coi là đã có account RBAC.
Phạm vi sản phẩm: [PRD](prd.md). Transition: [workflow](workflow.md).

## Ma trận quyền

Quyền mặc định là từ chối. “Được giao” nghĩa là assignee hiện tại của hồ sơ, được
kiểm tra tại backend cả khi đọc, ghi, tìm kiếm, thống kê và truy cập artifact.

| Hành động | Analyst | Manager | Admin |
|---|---|---|---|
| Đăng nhập, đăng xuất | Có | Có | Có |
| Danh sách/chi tiết hồ sơ | Được giao | Toàn bộ | Không |
| Score, explanation, model metadata | Theo quyền hồ sơ | Có | Không |
| Báo cáo model tổng hợp | Có | Có | Không |
| Import demo/CSV, retry job đã lỗi | Không | Có | Không |
| Phân công/đổi Analyst | Không | Có | Không |
| Bắt đầu review, ghi chú/soạn đề xuất | Được giao, `IN_REVIEW` | Không | Không |
| Submit đề xuất | Được giao, `IN_REVIEW` | Không | Không |
| Trả lại review, kèm lý do | Không | `PENDING_DECISION` | Không |
| Chốt quyết định mô phỏng | Không | Theo guards bên dưới | Không |
| Xem lịch sử nghiệp vụ/workload | Theo quyền hồ sơ | Toàn bộ | Không |
| Tạo/vô hiệu hóa tài khoản, reset password | Không | Không | Có |
| Đổi role | Không | Không | Có, có hạn chế |
| Xem audit quản trị tài khoản | Không | Không | Có |
| Sửa/xóa audit hoặc quyết định cuối | Không | Không | Không |

Admin có thể xem thông tin tài khoản để quản trị, không có quyền đọc nội dung
hồ sơ. Manager cần danh sách Analyst hoạt động để phân công, không được đọc
password/session hoặc tự quản lý tài khoản.

## Quy tắc bắt buộc

1. Mỗi tài khoản một role trong MVP. Admin không phải business superuser.
2. Cấm tự đổi role; cấm vô hiệu hóa/hạ quyền Admin hoạt động cuối cùng. Kiểm tra
   trường hợp hai Admin cập nhật đồng thời bằng transaction phù hợp.
3. Đổi role, disable account hoặc reset password thu hồi session hiện có. Mỗi
   request phải kiểm tra tài khoản còn hoạt động và quyền hiện tại.
4. Người từng thực hiện review một hồ sơ không được chốt hồ sơ đó, kể cả đổi
   từ Analyst sang Manager. Lưu actor identity lịch sử, không chỉ role hiện tại.
   Tính cả người đã bắt đầu review/ghi chú, không chỉ tác giả bản submit cuối.
5. Manager không sửa nội dung Analyst. Trả lại và ghi quyết định là hành động riêng.
6. Assignee bị disable/đổi role mất quyền ngay; hồ sơ giữ lịch sử và Manager phải
   phân công lại nếu review chưa hoàn tất. Submission bất biến đã có vẫn được
   Manager xem xét nếu còn là submission hiện hành hợp lệ.
7. Worker có service identity kỹ thuật, không được tạo human review/decision
   hoặc thay role. Không gán worker một tài khoản Manager để tiện thực hiện job.

Separation of duties ở cấp tài khoản không bảo đảm chống một người dùng nhiều
tài khoản hoặc database/VPS administrator can thiệp. Quản trị viên vận hành là
trust boundary riêng; thao tác đặc quyền cần được ghi nhận trong runbook.

## Authorization phía server

```text
Session → active account → role permission → object access
        → transition guard → separation of duties → transaction + audit
```

- FastAPI quyết định quyền cuối cùng; Next.js guards chỉ phục vụ UX.
- Không tin `actor_id`, `role`, `review_author` từ client. Actor lấy từ session.
- Payload chỉ được sửa các field cho phép, không mass-assign state/decision.
- Chưa đăng nhập: 401. Không đủ quyền chức năng: 403. Hồ sơ ngoài phạm vi truy cập:
  404 để không xác nhận sự tồn tại. Sai state/revision: 409. Payload sai: 422.
- Cấm truy cập snapshot bằng URL public/static path. Download cũng kiểm tra quyền.
- Request bị từ chối không được thay đổi dữ liệu nghiệp vụ; ghi security log
  phù hợp, không ghi payload, password hoặc token.

## Session và tài khoản

MVP dùng local accounts, password hashing Argon2id qua thư viện được duy trì và
opaque session phía server trong PostgreSQL. Cookie `HttpOnly`, `Secure`,
`SameSite`, có expiration/revocation. Có CSRF protection và kiểm tra Origin cho
mutation dùng cookie; rate limit login. Không lưu token trong localStorage.

Không public signup hoặc public Admin credentials. Bootstrap Admin đầu tiên bằng
thao tác vận hành một lần, không dùng password mặc định trong image/repository.
Reset password cần audit và thông tin tạm thời không được ghi vào log.

## Audit

- Ghi actor/service identity, action, resource ID, UTC timestamp, request/job ID,
  kết quả và metadata thay đổi tối thiểu; quyết định/return/reassign phải có lý do.
- Mutation nghiệp vụ và audit tương ứng nằm trong cùng transaction.
- Session/auth failures và significant reads dùng access/security logs riêng;
  không ghi toàn bộ dữ liệu hồ sơ vào audit chỉ để có before/after snapshot.
- Runtime không có quyền update/delete audit. Migration dùng credential riêng.
- Audit quản trị tài khoản và audit hồ sơ có quyền đọc riêng theo ma trận trên.

Kiểm thử negative cases, role switching, cross-object access và concurrency theo
[acceptance criteria](acceptance.md), không chỉ kiểm tra nút bị ẩn trên UI.
