# Ảnh giao diện cho README

Ba ảnh chụp thật bằng Playwright trên Docker demo local tại
`http://localhost:3000`, ngày 2026-11-16. Frontend production build từ source
hiện hành; API, PostgreSQL và worker thực, không mock responses.

- `overview.jpg`: dashboard của Manager.
- `case-review.jpg`: prediction, provenance, explanation và phân công.
- `mobile-review.jpg`: workspace review của Analyst trên viewport 390px.

Hồ sơ, tài khoản và LightGBM bundle đều synthetic. Điểm số trong ảnh không
đại diện metrics Home Credit đã đánh giá. Không có dữ liệu người vay thật,
secrets hoặc ảnh tạo bởi AI. Không chỉnh sửa nội dung ảnh; chỉ đặt viewport,
ẩn animation khi chụp và dùng JPEG để giảm dung lượng.
