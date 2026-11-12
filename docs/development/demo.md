# Kịch bản demo MVP (synthetic)

Kịch bản này dùng đúng workflow đã có E2E tự động
(`apps/web/tests/workflow.spec.ts`). Mọi dữ liệu là synthetic; quyết định là
mô phỏng hỗ trợ quyết định, không phải phê duyệt tín dụng thực tế.

## Chuẩn bị

1. Làm theo [application guide](application.md): `make app-db`,
   `make app-migrate`, bootstrap Admin, rồi chạy API, worker và web với
   catalog/model đã ghim của operator.
2. Admin đăng nhập, tạo một Manager và một Analyst (mật khẩu chuyển qua kênh
   riêng, không ghi vào tài liệu/video).

## Kịch bản quay demo

1. **Manager import:** đăng nhập Manager → *Import hồ sơ* → chọn catalog →
   tải CSV demo → trạng thái chuyển `SUCCEEDED` với 2 READY / 1 INCOMPLETE.
   Chỉ vào hồ sơ INCOMPLETE: thiếu nguồn bắt buộc nên bị chặn scoring; thiếu
   nguồn không đồng nghĩa không có lịch sử tín dụng.
2. **Chấm điểm:** mở *Hồ sơ*, chờ scoring `SUCCEEDED`, mở chi tiết: xác suất
   model, ngưỡng/chính sách, model/run/lock, cảnh báo và đóng góp log-odds.
   Nhấn mạnh explanation không phải quan hệ nhân quả hay lý do pháp lý.
3. **Phân công:** Manager chọn Analyst đang hoạt động, nhập lý do, lưu.
4. **Analyst review:** đăng nhập Analyst (chỉ thấy hồ sơ được giao) → bắt đầu
   review → ghi nhận xét, chọn đề xuất → gửi. Analyst không có nút chốt và
   gọi thẳng API decide bị từ chối (đã có test tự động).
5. **Manager quyết định:** mở lại hồ sơ, đọc đề xuất, nhập lý do, xác nhận
   chốt quyết định mô phỏng. Màn hình cuối ghi quyết định đã khóa, không mở
   lại/sửa được; lịch sử hiển thị toàn bộ sự kiện và actor.
6. **Admin:** đăng nhập Admin chỉ thấy quản lý tài khoản, không có đường vào
   hồ sơ; đổi role/vô hiệu hóa tài khoản thu hồi phiên ngay.

## Giới hạn phải nói khi demo

- `TARGET` là khó khăn thanh toán trong dữ liệu lịch sử, không phải xác suất
  vỡ nợ thực tế đã kiểm định.
- Điểm số không tự tạo quyết định; đề xuất Analyst, output model và quyết
  định Manager là ba khái niệm khác nhau.
- Demo local chưa phải bằng chứng deploy VPS/TLS/backup ngoài VPS; các hạng
  mục đó thuộc nghiệm thu B07 trên môi trường thật.
