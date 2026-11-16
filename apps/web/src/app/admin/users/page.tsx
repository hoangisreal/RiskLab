"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, ApiError, mutate, messageFor } from "@/lib/api";
import {
  ACTION_LABEL,
  ROLE_LABEL,
  formatDateTime,
  labelOf,
} from "@/lib/labels";
import { useSession, type User } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PageHeader, StatusBadge } from "@/components/primitives";

type UsersPage = {
  data: User[];
  pagination: { page: number; page_size: number; total: number };
};
type Audit = {
  id: string;
  action: string;
  created_at: string;
  reason: string | null;
};

export default function Users() {
  const { user, pending, refresh } = useSession();
  const [rows, setRows] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<User | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [auditPending, setAuditPending] = useState(false);
  const auditRequest = useRef(0);
  const selectedHeading = useRef<HTMLHeadingElement>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (selected?.id) selectedHeading.current?.focus();
  }, [selected?.id]);

  useEffect(() => {
    if (user?.role !== "ADMIN") return;
    const controller = new AbortController();
    const params = new URLSearchParams({ page: String(page), page_size: "10" });
    if (query) params.set("q", query);
    if (role) params.set("role", role);
    // Preserve the list envelope for pagination; the general client unwraps single-object data.
    void fetch(`/api/v1/admin/users?${params}`, {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 401) await refresh();
          const body = await response.json().catch(() => null);
          throw new ApiError(
            response.status,
            body?.error?.code ?? "CONNECTION_ERROR",
          );
        }
        return response.json() as Promise<UsersPage>;
      })
      .then((result) => {
        if (!controller.signal.aborted) {
          setRows(result.data);
          setTotal(result.pagination.total);
          setError("");
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(messageFor(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [user?.id, user?.role, page, query, role, refreshKey, refresh]);

  function reload() {
    setLoading(true);
    setRefreshKey((value) => value + 1);
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await mutate<User>("/api/v1/admin/users", {
        username: values.get("username"),
        password: values.get("password"),
        role: values.get("role"),
      });
      form.reset();
      setMessage("Đã tạo tài khoản. Chuyển mật khẩu qua kênh riêng an toàn.");
      reload();
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof ApiError && failure.status === 401)
        await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function change(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const values = new FormData(event.currentTarget);
    const active = values.get("is_active") === "true";
    if (
      selected.is_active &&
      !active &&
      !window.confirm(
        `Vô hiệu hóa ${selected.username} và thu hồi toàn bộ phiên?`,
      )
    )
      return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await mutate<User>(
        `/api/v1/admin/users/${selected.id}`,
        {
          expected_revision: selected.revision,
          reason: values.get("reason"),
          role: values.get("role"),
          is_active: active,
        },
        "PATCH",
      );
      await showAudit(updated);
      setMessage("Đã cập nhật và thu hồi các phiên của tài khoản.");
      reload();
      await refresh();
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof ApiError && failure.status === 401)
        await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await mutate(`/api/v1/admin/users/${selected.id}/reset-password`, {
        expected_revision: selected.revision,
        reason: values.get("reason"),
        new_password: values.get("new_password"),
      });
      form.reset();
      auditRequest.current += 1;
      setSelected(null);
      setMessage("Đã đặt lại mật khẩu và thu hồi các phiên.");
      reload();
      await refresh();
    } catch (failure) {
      setError(messageFor(failure));
      if (failure instanceof ApiError && failure.status === 401)
        await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function showAudit(target: User) {
    const request = ++auditRequest.current;
    setSelected(target);
    setAudit([]);
    setAuditPending(true);
    setError("");
    try {
      const result = await api<Audit[]>(
        `/api/v1/admin/audit-events?user_id=${target.id}`,
      );
      if (request === auditRequest.current) setAudit(result);
    } catch (failure) {
      if (request === auditRequest.current) setError(messageFor(failure));
    } finally {
      if (request === auditRequest.current) setAuditPending(false);
    }
  }

  if (pending)
    return <LoadingState>Đang kiểm tra quyền truy cập…</LoadingState>;
  if (!user)
    return (
      <>
        <h1>Phiên đăng nhập không còn hiệu lực</h1>
        <p>Đăng nhập lại để tiếp tục quản lý tài khoản.</p>
        <Link href="/login">Đăng nhập</Link>
      </>
    );
  if (user.role !== "ADMIN")
    return (
      <>
        <h1>Không có quyền quản trị tài khoản</h1>
        <p>
          Chức năng này chỉ dành cho Admin. Quyền cũng được kiểm tra tại API.
        </p>
        <Link href="/">Về tổng quan</Link>
      </>
    );

  return (
    <>
      <PageHeader
        title="Quản lý tài khoản"
        intro="Admin quản lý quyền truy cập, không được chốt quyết định nghiệp vụ theo quyền mặc định. Tài khoản đang chọn được tô sáng; mọi thay đổi thu hồi phiên ngay."
      />
      {error && <ErrorState retry={reload}>{error}</ErrorState>}
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      <section className="sheet" aria-label="Danh sách tài khoản">
        <div className="filterbar">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              setQuery(String(data.get("q") ?? ""));
              setPage(1);
              setLoading(true);
            }}
          >
            <label htmlFor="q">Tìm tên đăng nhập</label>
            <div className="inline">
              <input id="q" name="q" maxLength={64} />
              <button type="submit">Tìm kiếm</button>
            </div>
          </form>
          <div>
            <label htmlFor="role-filter">Vai trò</label>
            <select
              id="role-filter"
              value={role}
              onChange={(event) => {
                setRole(event.target.value);
                setPage(1);
                setLoading(true);
              }}
            >
              <option value="">Tất cả</option>
              <option>ADMIN</option>
              <option>MANAGER</option>
              <option>ANALYST</option>
            </select>
          </div>
        </div>
        <p>{total} tài khoản</p>
        {loading ? (
          <LoadingState />
        ) : rows.length === 0 ? (
          <EmptyState>Không có tài khoản khớp bộ lọc.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Tên đăng nhập</th>
                  <th>Vai trò</th>
                  <th>Trạng thái</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    aria-current={selected?.id === row.id ? "true" : undefined}
                  >
                    <td data-label="Tên đăng nhập">
                      <strong>{row.username}</strong>
                      {selected?.id === row.id ? " (đang chọn)" : ""}
                    </td>
                    <td data-label="Vai trò">
                      <StatusBadge
                        value={row.role}
                        label={ROLE_LABEL[row.role] ?? row.role}
                      />
                    </td>
                    <td data-label="Trạng thái">
                      <StatusBadge
                        value={row.is_active ? "READY" : "INVALID"}
                        label={
                          row.is_active ? "Đang hoạt động" : "Đã vô hiệu hóa"
                        }
                      />
                    </td>
                    <td data-label="Thao tác">
                      <button
                        disabled={busy}
                        onClick={() => void showAudit(row)}
                      >
                        Quản lý {row.username}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <button
            disabled={page === 1 || loading}
            onClick={() => {
              setPage((value) => value - 1);
              setLoading(true);
            }}
          >
            Trang trước
          </button>
          <span>Trang {page}</span>
          <button
            disabled={page * 10 >= total || loading}
            onClick={() => {
              setPage((value) => value + 1);
              setLoading(true);
            }}
          >
            Trang sau
          </button>
        </div>
      </section>
      {selected && (
        <section
          className="sheet section-gap"
          aria-label={`Quản lý ${selected.username}`}
        >
          <h2 ref={selectedHeading} tabIndex={-1}>
            {selected.username}
          </h2>
          <p>
            Revision {selected.revision}. Thay đổi quyền hoặc mật khẩu sẽ thu
            hồi phiên. Không thể tự đổi vai trò hoặc vô hiệu hóa Admin cuối.
          </p>
          <form
            className="form-sheet"
            onSubmit={change}
            key={`${selected.id}-${selected.revision}`}
          >
            <div className="field">
              <label htmlFor="edit-role">Vai trò tài khoản</label>
              <select id="edit-role" name="role" defaultValue={selected.role}>
                <option>ADMIN</option>
                <option>MANAGER</option>
                <option>ANALYST</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="active">Trạng thái tài khoản</label>
              <select
                id="active"
                name="is_active"
                defaultValue={String(selected.is_active)}
              >
                <option value="true">Đang hoạt động</option>
                <option value="false">Vô hiệu hóa</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="change-reason">Lý do thay đổi</label>
              <textarea
                id="change-reason"
                name="reason"
                required
                maxLength={2000}
              />
            </div>
            <button disabled={busy}>Lưu quyền và trạng thái</button>
          </form>
          <details className="section-gap">
            <summary>Đặt lại mật khẩu</summary>
            <form className="form-sheet" onSubmit={resetPassword}>
              <div className="field">
                <label htmlFor="new-password">Mật khẩu mới</label>
                <input
                  id="new-password"
                  type="password"
                  name="new_password"
                  autoComplete="new-password"
                  minLength={12}
                  maxLength={128}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="reset-reason">Lý do đặt lại</label>
                <textarea
                  id="reset-reason"
                  name="reason"
                  required
                  maxLength={2000}
                />
              </div>
              <button disabled={busy}>Đặt lại và thu hồi phiên</button>
            </form>
          </details>
          <h2 className="section-gap">Lịch sử quản trị</h2>
          {auditPending ? (
            <LoadingState>Đang tải lịch sử…</LoadingState>
          ) : audit.length ? (
            <ol className="timeline">
              {audit.map((event) => (
                <li key={event.id}>
                  <strong>{labelOf(ACTION_LABEL, event.action)}</strong>{" "}
                  <span className="event-meta">
                    ·{" "}
                    <time title={event.created_at}>
                      {formatDateTime(event.created_at)}
                    </time>
                  </span>
                  <div className="event-meta">
                    <code>{event.action}</code> · id {event.id.slice(0, 8)}…
                  </div>
                  {event.reason ? <p>{event.reason}</p> : ""}
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState>Chưa có lịch sử để hiển thị.</EmptyState>
          )}
        </section>
      )}
      <section className="sheet section-gap" aria-labelledby="new-user">
        <h2 id="new-user">Tạo tài khoản</h2>
        <form className="form-sheet" onSubmit={create}>
          <div className="field">
            <label htmlFor="new-username">Tên đăng nhập mới</label>
            <input
              id="new-username"
              name="username"
              required
              minLength={3}
              maxLength={64}
              pattern="[a-z0-9_.\-]{3,64}"
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label htmlFor="initial-password">Mật khẩu khởi tạo</label>
            <input
              id="initial-password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
            />
          </div>
          <div className="field">
            <label htmlFor="new-role">Vai trò ban đầu</label>
            <select id="new-role" name="role" defaultValue="ANALYST">
              <option>ANALYST</option>
              <option>MANAGER</option>
              <option>ADMIN</option>
            </select>
          </div>
          <button disabled={busy}>Tạo tài khoản</button>
        </form>
      </section>
    </>
  );
}
