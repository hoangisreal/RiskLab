/** Same-origin browser client. All authentication and business authority remains in FastAPI. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public requestId?: string,
  ) {
    super(code);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  if (!path.startsWith("/api/v1/") || path.includes("://"))
    throw new Error("Invalid API path");
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.code ?? "CONNECTION_ERROR",
      response.headers.get("x-request-id") ?? undefined,
    );
  }
  return body?.data as T;
}

let csrfToken: string | undefined;

export function setCsrfToken(token: string | undefined) {
  csrfToken = token;
}

/** Retry only a rejected CSRF check, never an ambiguous business mutation or network error. */
export async function mutate<T>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  return csrfRequest<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function csrfRequest<T>(path: string, options: RequestInit): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (!csrfToken)
      csrfToken = (await api<{ csrf_token: string }>("/api/v1/auth/csrf"))
        .csrf_token;
    try {
      return await api<T>(path, {
        ...options,
        headers: { ...options.headers, "X-CSRF-Token": csrfToken },
      });
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== "CSRF_FAILED")
        throw error;
      csrfToken = undefined;
      if (attempt === 1) throw error;
    }
  }
  throw new Error("Request could not be completed");
}

type PageResult<T> = {
  data: T[];
  pagination: { page: number; page_size: number; total: number };
};

/** List endpoints keep their pagination envelope; single-object calls use api(). */
export async function apiPage<T>(path: string): Promise<PageResult<T>> {
  if (!path.startsWith("/api/v1/") || path.includes("://"))
    throw new Error("Invalid API path");
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.code ?? "CONNECTION_ERROR",
      response.headers.get("x-request-id") ?? undefined,
    );
  }
  return body as PageResult<T>;
}

/** Multipart import: the browser sets the boundary; CSRF and idempotency stay explicit. */
export async function uploadImport<T>(
  catalogVersion: string,
  file: File,
  idempotencyKey: string,
): Promise<T> {
  const form = new FormData();
  form.set("import_contract_version", "demo-import/1");
  form.set("catalog_version", catalogVersion);
  form.set("file", file);
  return csrfRequest<T>("/api/v1/imports", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: form,
  });
}

/** Keep the same payload and key when recovering a rejected CSRF check. */
export async function mutateWithKey<T>(
  path: string,
  body: unknown,
  idempotencyKey: string,
): Promise<T> {
  return csrfRequest<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
}

export function messageFor(error: unknown): string {
  if (!(error instanceof ApiError))
    return "Không kết nối được máy chủ. Kiểm tra kết nối và thử lại.";
  const messages: Record<string, string> = {
    AUTHENTICATION_REQUIRED:
      "Phiên đăng nhập đã hết hạn hoặc bị thu hồi. Hãy đăng nhập lại.",
    INVALID_CREDENTIALS: "Tên đăng nhập hoặc mật khẩu không hợp lệ.",
    FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
    SEPARATION_OF_DUTIES:
      "Người đã tham gia review không được chốt quyết định cho hồ sơ này.",
    CSRF_FAILED: "Phiên bảo vệ đã thay đổi. Tải lại trang rồi thử lại.",
    REVISION_CONFLICT:
      "Dữ liệu đã thay đổi. Tải lại trước khi tiếp tục, không tự gửi lại thao tác cũ.",
    INVALID_STATE:
      "Trạng thái hồ sơ không cho phép thao tác này. Tải lại để xem trạng thái mới nhất.",
    IDEMPOTENCY_CONFLICT:
      "Khóa idempotency đã dùng cho nội dung khác. Tạo thao tác mới nếu cần.",
    NOT_FOUND:
      "Không tìm thấy hồ sơ hoặc hồ sơ ngoài phạm vi truy cập của bạn.",
    RESOURCE_CONFLICT: "Tên đăng nhập đã tồn tại.",
    LAST_ADMIN: "Không thể vô hiệu hóa Admin hoạt động cuối cùng.",
    VALIDATION_ERROR: "Dữ liệu không hợp lệ. Kiểm tra lại các trường nhập.",
    UNSUPPORTED_CONTRACT: "Phiên bản contract không được hỗ trợ.",
    RATE_LIMITED: "Quá nhiều yêu cầu. Vui lòng chờ rồi thử lại.",
    PAYLOAD_TOO_LARGE: "Tệp vượt quá giới hạn cho phép.",
    MODEL_UNAVAILABLE:
      "Model chưa sẵn sàng hoặc không tương thích. Hồ sơ chưa được chấm điểm.",
    DEPENDENCY_UNAVAILABLE:
      "Cơ sở dữ liệu chưa sẵn sàng. Liên hệ người vận hành.",
  };
  return (
    messages[error.code] ??
    "Không hoàn tất được yêu cầu. Thử lại hoặc liên hệ người vận hành."
  );
}
