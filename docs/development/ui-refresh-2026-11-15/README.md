# Bằng chứng hình ảnh nâng cấp UI/UX — 2026-11-15

Ảnh chụp bản sau đợt nâng cấp UI/UX (D37–D38) tại thư mục `after/`. Mỗi ảnh
chụp viewport đầy đủ 1440×900, 768×900 hoặc 390×900; hậu tố tên file là
chiều rộng. Ảnh bản trước (HEAD `974b626`) đã bỏ theo D40; khi cần đối chiếu,
dựng lại bản trước từ HEAD đó theo cách chụp bên dưới.

## Cách chụp (tái lập được)

- Bản sau chạy từ working tree hiện tại với backend `scripts.browser_backend`
  riêng (database disposable, tài khoản `*.browser`, model synthetic) và dev
  server riêng.
- Đăng nhập và chuẩn bị dữ liệu (import 3 hồ sơ → scoring → phân công cho
  `analyst.browser`) thực hiện qua API cùng origin; ảnh chụp bằng Playwright
  của repo, định dạng JPEG chất lượng 60. Script chụp là file tạm ngoài repo,
  không commit.
- Trạng thái `loading`/`error`/`empty` dùng route interception trên trang Hồ
  sơ (làm chậm/hủy API) và backend mới chưa import; các ảnh còn lại chụp sau
  khi đã seed dữ liệu.
- Timestamp, ID rút gọn và hash trong ảnh là dữ liệu synthetic của backend
  riêng, không phải khác biệt UI.

## Ma trận ảnh

### Đăng nhập (chưa đăng nhập, không sidebar nghiệp vụ)

| Màn hình | 1440 | 768 | 390 |
|---|---|---|---|
| Đăng nhập | [Ảnh](after/login-1440.jpg) | [Ảnh](after/login-768.jpg) | [Ảnh](after/login-390.jpg) |

### Vai trò Manager

| Màn hình | 1440 | 768 | 390 |
|---|---|---|---|
| Tổng quan + hành động tiếp theo | [Ảnh](after/manager-home-1440.jpg) | [Ảnh](after/manager-home-768.jpg) | [Ảnh](after/manager-home-390.jpg) |
| Danh sách hồ sơ (đã seed) | [Ảnh](after/manager-apps-1440.jpg) | [Ảnh](after/manager-apps-768.jpg) | [Ảnh](after/manager-apps-390.jpg) |
| Hàng đợi review | [Ảnh](after/manager-queue-1440.jpg) | [Ảnh](after/manager-queue-768.jpg) | [Ảnh](after/manager-queue-390.jpg) |
| Chi tiết hồ sơ | [Ảnh](after/manager-detail-1440.jpg) | [Ảnh](after/manager-detail-768.jpg) | [Ảnh](after/manager-detail-390.jpg) |
| Import | [Ảnh](after/manager-imports-1440.jpg) | [Ảnh](after/manager-imports-768.jpg) | [Ảnh](after/manager-imports-390.jpg) |
| Model | [Ảnh](after/manager-models-1440.jpg) | [Ảnh](after/manager-models-768.jpg) | [Ảnh](after/manager-models-390.jpg) |
| Báo cáo | [Ảnh](after/manager-reports-1440.jpg) | [Ảnh](after/manager-reports-768.jpg) | [Ảnh](after/manager-reports-390.jpg) |

### Vai trò Analyst (hồ sơ đã được phân công)

| Màn hình | 1440 | 768 | 390 |
|---|---|---|---|
| Tổng quan | [Ảnh](after/analyst-home-1440.jpg) | [Ảnh](after/analyst-home-768.jpg) | [Ảnh](after/analyst-home-390.jpg) |
| Hàng đợi review | [Ảnh](after/analyst-queue-1440.jpg) | [Ảnh](after/analyst-queue-768.jpg) | [Ảnh](after/analyst-queue-390.jpg) |
| Chi tiết hồ sơ | [Ảnh](after/analyst-detail-1440.jpg) | [Ảnh](after/analyst-detail-768.jpg) | [Ảnh](after/analyst-detail-390.jpg) |

### Vai trò Admin

| Màn hình | 1440 | 768 | 390 |
|---|---|---|---|
| Quản lý tài khoản | [Ảnh](after/admin-users-1440.jpg) | [Ảnh](after/admin-users-768.jpg) | [Ảnh](after/admin-users-390.jpg) |
| Nhật ký audit | [Ảnh](after/admin-audit-1440.jpg) | [Ảnh](after/admin-audit-768.jpg) | [Ảnh](after/admin-audit-390.jpg) |

### Trạng thái tải / trống / lỗi (trang Hồ sơ, vai trò Manager)

| Trạng thái | 1440 | 768 | 390 |
|---|---|---|---|
| Đang tải (skeleton) | [Ảnh](after/state-loading-apps-1440.jpg) | [Ảnh](after/state-loading-apps-768.jpg) | [Ảnh](after/state-loading-apps-390.jpg) |
| Không có dữ liệu | [Ảnh](after/state-empty-apps-1440.jpg) | [Ảnh](after/state-empty-apps-768.jpg) | [Ảnh](after/state-empty-apps-390.jpg) |
| Tải lỗi + thử lại | [Ảnh](after/state-error-apps-1440.jpg) | [Ảnh](after/state-error-apps-768.jpg) | [Ảnh](after/state-error-apps-390.jpg) |

## Đối chiếu với bản trước (ghi nhận tại thời điểm nâng cấp)

- Navigation: tiếng Anh + workspace switcher giả (trước) → tiếng Việt theo
  role, thêm Báo cáo, drawer mobile có Escape (sau).
- Badge trạng thái: mã enum thô (trước) → icon + nhãn Việt + mã enum giữ
  nguyên cho kiểm thử (sau).
- Tiến trình hồ sơ: 4 bước từ ASSIGNED (trước) → 5 bước kể cả chưa phân công (sau).
- Probability/ngưỡng: thập phân thô (trước) → phần trăm (sau).
- Explanation: bảng log-odds (trước) → thanh đóng góp có dấu + giá trị (sau);
  provenance thu gọn trong `<details>`, cảnh báo thiếu nguồn luôn hiện.
- Danh sách hồ sơ: filter đổi là chạy (trước) → submit mới chạy, filter lưu
  URL, có xóa bộ lọc và đếm kết quả (sau).
- Kiểm tra tự động đi kèm: E2E drawer mobile + Escape, filter URL/back-nav và
  giữ draft khi đóng/mở chi tiết (`apps/web/tests/redesign.spec.ts` và bước
  bổ sung trong `workflow.spec.ts`); không tràn ngang ở cả ba kích thước.
