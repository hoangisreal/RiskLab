/** Vietnamese display labels for backend enums. Payloads and routes stay unchanged. */

export const REVIEW_STATE_LABEL: Record<string, string> = {
  UNASSIGNED: "Chưa phân công",
  ASSIGNED: "Đã phân công",
  IN_REVIEW: "Đang review",
  PENDING_DECISION: "Chờ quyết định",
  DECIDED: "Đã quyết định",
};

export const SCORING_STATE_LABEL: Record<string, string> = {
  NOT_REQUESTED: "Chưa yêu cầu",
  QUEUED: "Đang chờ",
  RUNNING: "Đang chấm",
  SUCCEEDED: "Đã có điểm",
  FAILED: "Chấm điểm lỗi",
};

export const FEATURE_AVAILABILITY_LABEL: Record<string, string> = {
  READY: "Đủ dữ liệu",
  INCOMPLETE: "Thiếu nguồn",
  INVALID: "Không hợp lệ",
};

export const ROLE_LABEL: Record<string, string> = {
  ADMIN: "Quản trị viên",
  MANAGER: "Quản lý",
  ANALYST: "Chuyên viên phân tích",
};

export const PROPOSAL_LABEL: Record<string, string> = {
  APPROVE: "Đề xuất duyệt",
  DECLINE: "Đề xuất từ chối",
};

export const OUTCOME_LABEL: Record<string, string> = {
  APPROVED_SIMULATED: "Duyệt (mô phỏng)",
  DECLINED_SIMULATED: "Từ chối (mô phỏng)",
};

export const IMPORT_STATE_LABEL: Record<string, string> = {
  QUEUED: "Đang chờ",
  RUNNING: "Đang xử lý",
  SUCCEEDED: "Hoàn tất",
  FAILED: "Thất bại",
};

/** Short Vietnamese descriptions for audit/workflow actions shown in timelines. */
export const ACTION_LABEL: Record<string, string> = {
  "user.bootstrapped": "Khởi tạo Admin đầu tiên",
  "user.local_demo_created": "Tạo tài khoản demo",
  "user.docker_demo_created": "Tạo tài khoản demo",
  "user.created": "Tạo tài khoản",
  "user.role_changed": "Đổi vai trò",
  "user.status_changed": "Đổi trạng thái tài khoản",
  "user.password_reset": "Đặt lại mật khẩu",
  "session.login": "Đăng nhập",
  "session.logout": "Đăng xuất",
  "session.revoked": "Thu hồi phiên",
  "import.created": "Tạo import",
  "import.succeeded": "Import hoàn tất",
  "import.failed": "Import thất bại",
  "import.retry_requested": "Yêu cầu thử lại import",
  "application.assigned": "Phân công hồ sơ",
  "application.scored": "Chấm điểm xong",
  "review.started": "Bắt đầu review",
  "review.updated": "Cập nhật review",
  "review.submitted": "Gửi đề xuất",
  "review.returned": "Trả review",
  "decision.created": "Chốt quyết định",
  "scoring.retry_requested": "Yêu cầu chấm lại",
};

export function labelOf(
  map: Record<string, string>,
  value: string | null | undefined,
): string {
  if (!value) return "—";
  return map[value] ?? value;
}

/** Percentage with one decimal, e.g. 0.0804 -> "8.0%". */
export function toPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Compact UTC timestamp for timelines, e.g. "15/11/2026 11:07". */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = String(date.getUTCDate()).padStart(2, "0");
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const year = date.getUTCFullYear();
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");
  return `${day}/${month}/${year} ${hour}:${minute} UTC`;
}
