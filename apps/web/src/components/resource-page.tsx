"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "./session";
import { api, apiPage, messageFor } from "@/lib/api";
import { LoadingState, ErrorState, EmptyState } from "./states";
import { DataTable } from "./data-table";
import { CountChart } from "./risk-chart";
import { Badge } from "./ui/badge";
type Workload = {
  total: number;
  by_review_state: { value: string; count: number }[];
};
type Model = {
  model_version: string;
  model_sha256: string;
  model_run_id: string;
  lock_id: string;
  feature_contract_version: string;
  limitations: string[];
};
type Audit = {
  id: string;
  action: string;
  resource_id: string;
  actor_id: string;
  created_at: string;
  reason: string;
};
export function ResourcePage({
  kind,
}: {
  kind: "overview" | "models" | "audit" | "settings";
}) {
  const { user } = useSession();
  return (
    <ResourceWorkspace key={`${kind}:${user?.id ?? "anonymous"}`} kind={kind} />
  );
}

function ResourceWorkspace({
  kind,
}: {
  kind: "overview" | "models" | "audit" | "settings";
}) {
  const { user, pending } = useSession();
  const [workload, setWorkload] = useState<Workload | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [events, setEvents] = useState<Audit[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  useEffect(() => {
    let cancelled = false;
    if (
      !user ||
      kind === "settings" ||
      (user.role === "ADMIN" && kind !== "audit")
    )
      return;
    void (async () => {
      try {
        if (kind === "audit") {
          if (user.role === "ADMIN") {
            const result = await apiPage<Audit>(
              `/api/v1/admin/audit-events?page=${page}&page_size=20`,
            );
            if (!cancelled) {
              setEvents(result.data);
              setTotal(result.pagination.total);
            }
          }
        } else if (kind === "models") {
          const m = await api<Model>("/api/v1/models/current");
          if (!cancelled) setModel(m);
        } else {
          const w = await api<Workload>("/api/v1/reports/workload");
          if (!cancelled) setWorkload(w);
          // Model metadata is optional and must not delay the workload view.
          void api<Model>("/api/v1/models/current")
            .then((m) => {
              if (!cancelled) setModel(m);
            })
            .catch(() => {
              if (!cancelled) setModel(null);
            });
        }
        if (!cancelled) setError("");
      } catch (e) {
        if (!cancelled) setError(messageFor(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, kind, refresh, page]);
  if (pending) return <LoadingState />;
  if (!user)
    return (
      <>
        <h1>Phiên đăng nhập không còn hiệu lực</h1>
        <Link href="/login">Đăng nhập lại</Link>
      </>
    );
  if (kind === "settings")
    return (
      <>
        <div className="page-heading">
          <p className="eyebrow">Workspace / Settings</p>
          <h1>Settings</h1>
          <p>Thông tin phiên và phạm vi quyền hiện tại.</p>
        </div>
        <section className="sheet">
          <h2>Tài khoản của bạn</h2>
          <dl>
            <div>
              <dt>Tên đăng nhập</dt>
              <dd>{user.username}</dd>
            </div>
            <div>
              <dt>Vai trò</dt>
              <dd>
                <Badge>{user.role}</Badge>
              </dd>
            </div>
            <div>
              <dt>Môi trường</dt>
              <dd>Decision-support simulation · Synthetic data</dd>
            </div>
          </dl>
          {user.role === "ADMIN" ? (
            <Link href="/admin/users">
              Quản lý tài khoản và quyền truy cập →
            </Link>
          ) : (
            <p>Liên hệ Admin để đổi quyền hoặc reset mật khẩu.</p>
          )}
        </section>
        <section className="sheet section-gap">
          <h2>Kiểm soát hệ thống</h2>
          <p>
            Secrets, model bundle, deployment và backup được operator cấu hình
            ngoài giao diện. Phiên làm việc được quản lý phía server; không có
            tùy chọn bỏ qua authorization.
          </p>
        </section>
      </>
    );
  if (user.role === "ADMIN" && kind !== "audit")
    return (
      <>
        <h1>Không có quyền nghiệp vụ</h1>
        <p>Admin quản lý hệ thống và tài khoản.</p>
        <Link href="/admin/users">Quản lý tài khoản</Link>
      </>
    );
  if (kind === "audit" && user.role !== "ADMIN")
    return (
      <>
        <h1>Audit Logs</h1>
        <section className="sheet">
          <h2>Lịch sử theo hồ sơ</h2>
          <p>
            Lịch sử nghiệp vụ nằm trong chi tiết từng hồ sơ và chỉ hiển thị
            trong phạm vi truy cập của bạn. Audit quản trị tài khoản dành riêng
            cho Admin.
          </p>
          <Link href="/applications">Chọn hồ sơ để xem lịch sử →</Link>
        </section>
      </>
    );
  const title =
    kind === "overview"
      ? "Overview"
      : kind === "models"
        ? "Model Registry"
        : "Audit Logs";
  return (
    <>
      <div className="page-heading">
        <p className="eyebrow">Workspace / {title}</p>
        <h1>{title}</h1>
        <p>
          {kind === "overview"
            ? "Khối lượng hồ sơ và tiến trình quyết định trong phạm vi quyền của bạn."
            : kind === "models"
              ? "Danh tính model đã ghim cho scoring; lịch sử prediction giữ nguyên version tại thời điểm xử lý."
              : "Lịch sử quản trị tài khoản · Dữ liệu chỉ đọc."}
        </p>
      </div>
      {loading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState
          retry={() => {
            setLoading(true);
            setError("");
            setRefresh((x) => x + 1);
          }}
        >
          {error}
        </ErrorState>
      ) : kind === "audit" ? (
        <section className="sheet">
          <form className="filterbar" onSubmit={(e) => e.preventDefault()}>
            <div className="field">
              <label htmlFor="audit-search">Tìm trong trang</label>
              <input
                id="audit-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="audit-action">Hành động</label>
              <select
                id="audit-action"
                value={action}
                onChange={(e) => setAction(e.target.value)}
              >
                <option value="">Tất cả trên trang</option>
                {Array.from(new Set(events.map((e) => e.action))).map((a) => (
                  <option key={a}>{a}</option>
                ))}
              </select>
            </div>
          </form>
          {events.length ? (
            <DataTable
              data={events.filter(
                (e) =>
                  (!action || e.action === action) &&
                  `${e.action} ${e.actor_id} ${e.reason ?? ""}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
              )}
              columns={[
                { accessorKey: "created_at", header: "Thời gian" },
                { accessorKey: "action", header: "Hành động" },
                { accessorKey: "actor_id", header: "Actor" },
                { accessorKey: "reason", header: "Lý do" },
              ]}
            />
          ) : (
            <EmptyState>Chưa có sự kiện.</EmptyState>
          )}
          <div className="pagination">
            <button
              disabled={page === 1}
              onClick={() => {
                setLoading(true);
                setError("");
                setPage((x) => x - 1);
              }}
            >
              Trang trước
            </button>
            <span>Trang {page}</span>
            <button
              disabled={page * 20 >= total}
              onClick={() => {
                setLoading(true);
                setError("");
                setPage((x) => x + 1);
              }}
            >
              Trang sau
            </button>
          </div>
        </section>
      ) : kind === "models" && model ? (
        <section className="sheet">
          <div className="inline">
            <h2>{model.model_version}</h2>
            <Badge variant="secondary">Pinned release</Badge>
          </div>
          <dl>
            {[
              ["Model SHA256", model.model_sha256],
              ["Run ID", model.model_run_id],
              ["Lock ID", model.lock_id],
              ["Feature contract", model.feature_contract_version],
            ].map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd className="break-all font-mono text-xs">{v}</dd>
              </div>
            ))}
          </dl>
          <p className="notice">
            Đăng ký, promote và rollback model là thao tác operator qua CLI có
            integrity gates và audit. Màn hình này chỉ đọc; metadata không chứng
            minh worker sẵn sàng.
          </p>
          <Link href="/reports">Xem báo cáo và bằng chứng model →</Link>
          {model.limitations.map((l) => (
            <p key={l}>{l}</p>
          ))}
        </section>
      ) : workload ? (
        <>
          <div className="metric-grid">
            {[
              ["Tổng hồ sơ", workload.total],
              [
                "Đang review",
                workload.by_review_state.find((x) => x.value === "IN_REVIEW")
                  ?.count ?? 0,
              ],
              [
                "Chờ Manager",
                workload.by_review_state.find(
                  (x) => x.value === "PENDING_DECISION",
                )?.count ?? 0,
              ],
              [
                "Đã quyết định",
                workload.by_review_state.find((x) => x.value === "DECIDED")
                  ?.count ?? 0,
              ],
            ].map(([k, v]) => (
              <div className="metric" key={k}>
                <small>{k}</small>
                <strong>{v}</strong>
              </div>
            ))}
          </div>
          <div className="detail-grid">
            <section className="sheet">
              <h2>Phân bố trạng thái review</h2>
              {workload.total ? (
                <CountChart data={workload.by_review_state} />
              ) : (
                <EmptyState>
                  Chưa có hồ sơ. Manager có thể import catalog synthetic.
                </EmptyState>
              )}
            </section>
            <section className="sheet">
              <p className="eyebrow">Next action</p>
              <h2>
                {user.role === "MANAGER"
                  ? "Review đề xuất của Analyst"
                  : "Tiếp tục hồ sơ được giao"}
              </h2>
              <p>
                Kiểm tra dữ liệu và prediction trước khi đưa ra nhận định. Mọi
                quyết định cuối đều được ghi audit.
              </p>
              <Link href="/review-queue">Mở Review Queue →</Link>
              <hr className="my-6 border-border" />
              <small>MODEL ĐANG GHIM</small>
              <p>{model?.model_version ?? "Metadata chưa sẵn sàng"}</p>
              <Badge variant="outline">ML prediction ≠ Human decision</Badge>
            </section>
          </div>
        </>
      ) : (
        <EmptyState>Chưa có dữ liệu.</EmptyState>
      )}
    </>
  );
}

export function OverviewDashboard() {
  const { user } = useSession();
  return user ? <ResourcePage kind="overview" /> : null;
}
