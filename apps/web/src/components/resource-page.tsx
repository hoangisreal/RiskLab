"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useSession } from "./session";
import { api, apiPage, messageFor } from "@/lib/api";
import { ACTION_LABEL, formatDateTime, labelOf } from "@/lib/labels";
import { LoadingState, ErrorState, EmptyState } from "./states";
import { DataTable } from "./data-table";
import { CountChart } from "./risk-chart";
import { MetricCard, PageHeader, StatusBadge } from "./primitives";

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
              setError("");
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
        <PageHeader
          title="Settings"
          intro="Thông tin phiên làm việc và phạm vi quyền của bạn. Secrets, model bundle và hạ tầng do operator quản lý, không chỉnh sửa tại đây."
        />
        <section className="sheet" aria-label="Tài khoản của bạn">
          <h2>Tài khoản của bạn</h2>
          <dl>
            <div>
              <dt>Tên đăng nhập</dt>
              <dd>{user.username}</dd>
            </div>
            <div>
              <dt>Vai trò</dt>
              <dd>
                <StatusBadge
                  value={user.role}
                  label={user.role}
                  showCode={false}
                />
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
      </>
    );
  if (user.role === "ADMIN" && kind !== "audit")
    return (
      <>
        <h1>Tổng quan quản trị</h1>
        <p>
          Admin quản lý hệ thống và tài khoản, không có dashboard nghiệp vụ.
        </p>
        <div className="next-actions">
          <Link href="/admin/users">
            Quản lý tài khoản <ArrowRight size={15} aria-hidden="true" />
          </Link>
          <Link href="/audit" className="secondary">
            Xem nhật ký audit <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </div>
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
            trong phạm vi truy cập của bạn. Nhật ký quản trị tài khoản dành
            riêng cho Admin.
          </p>
          <Link href="/applications">Chọn hồ sơ để xem lịch sử →</Link>
        </section>
      </>
    );
  if (loading) {
    return (
      <>
        <PageHeader
          title={
            kind === "models"
              ? "Model Registry"
              : kind === "audit"
                ? "Audit Logs"
                : "Tổng quan"
          }
        />
        <LoadingState />
      </>
    );
  }
  if (error) {
    return (
      <>
        <PageHeader
          title={
            kind === "models"
              ? "Model Registry"
              : kind === "audit"
                ? "Audit Logs"
                : "Tổng quan"
          }
        />
        <ErrorState
          retry={() => {
            setLoading(true);
            setError("");
            setRefresh((x) => x + 1);
          }}
        >
          {error}
        </ErrorState>
      </>
    );
  }
  if (kind === "audit") {
    const filtered = events.filter(
      (e) =>
        (!action || e.action === action) &&
        `${e.action} ${e.actor_id} ${e.reason ?? ""}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );
    return (
      <>
        <PageHeader
          title="Audit Logs"
          intro="Lịch sử quản trị tài khoản · Dữ liệu chỉ đọc. Mô tả tiếng Việt bên dưới chỉ để đọc; mã sự kiện gốc giữ nguyên để tra cứu."
        />
        <section className="sheet" aria-label="Nhật ký quản trị">
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
          {filtered.length ? (
            <DataTable
              data={filtered}
              columns={[
                {
                  accessorKey: "created_at",
                  header: "Thời điểm",
                  cell: ({ row }) => (
                    <time title={row.original.created_at}>
                      {formatDateTime(row.original.created_at)}
                    </time>
                  ),
                },
                {
                  accessorKey: "action",
                  header: "Sự kiện",
                  cell: ({ row }) => (
                    <span>
                      {labelOf(ACTION_LABEL, row.original.action)}{" "}
                      <code className="status-code">{row.original.action}</code>
                    </span>
                  ),
                },
                { accessorKey: "actor_id", header: "Người thực hiện" },
                { accessorKey: "reason", header: "Lý do" },
              ]}
            />
          ) : events.length === 0 ? (
            <EmptyState>Chưa có sự kiện.</EmptyState>
          ) : (
            <p>Không có kết quả phù hợp.</p>
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
            <span>
              Trang {page} · Tổng {total} sự kiện
            </span>
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
      </>
    );
  }
  if (kind === "models" && model) {
    return (
      <>
        <PageHeader
          title="Model Registry"
          intro="Danh tính model đã ghim cho scoring. Lịch sử prediction giữ nguyên version tại thời điểm xử lý; màn hình này chỉ đọc."
        />
        <section className="sheet" aria-label="Model đang ghim">
          <p className="eyebrow">Model đang ghim · Pinned release</p>
          <h2>
            <span>{model.model_version}</span>{" "}
            <StatusBadge
              value="SUCCEEDED"
              label="Đang phục vụ"
              showCode={false}
            />
          </h2>
          <dl>
            <div>
              <dt>Run / Lock</dt>
              <dd>
                {model.model_run_id} · {model.lock_id}
              </dd>
            </div>
            <div>
              <dt>Feature contract</dt>
              <dd>{model.feature_contract_version}</dd>
            </div>
          </dl>
          <details className="tech">
            <summary>Chi tiết kỹ thuật (hash, run, lock)</summary>
            <div>
              <p>
                Model SHA256: <code>{model.model_sha256}</code>
              </p>
              <p>
                Run ID: <code>{model.model_run_id}</code> · Lock ID:{" "}
                <code>{model.lock_id}</code>
              </p>
            </div>
          </details>
          <p className="notice">
            Đăng ký, promote và rollback model là thao tác operator qua CLI có
            integrity gates và audit. Metadata không chứng minh worker sẵn sàng.
          </p>
          <Link href="/reports">Xem báo cáo và bằng chứng model →</Link>
          {model.limitations.map((l) => (
            <p key={l}>{l}</p>
          ))}
        </section>
      </>
    );
  }
  if (workload) {
    const count = (value: string) =>
      workload.by_review_state.find((x) => x.value === value)?.count ?? 0;
    const nextHref =
      user.role === "MANAGER" ? "/review-queue" : "/review-queue";
    const nextLabel =
      user.role === "MANAGER"
        ? "Review đề xuất của Analyst"
        : "Tiếp tục hồ sơ được giao";
    return (
      <>
        <PageHeader
          title="Tổng quan"
          intro="Khối lượng hồ sơ và tiến trình quyết định trong phạm vi quyền của bạn."
        />
        <div className="next-actions" aria-label="Hành động tiếp theo">
          <Link href={nextHref}>
            {nextLabel} <ArrowRight size={15} aria-hidden="true" />
          </Link>
          {user.role === "MANAGER" ? (
            <Link href="/imports" className="secondary">
              Import hồ sơ mới <ArrowRight size={15} aria-hidden="true" />
            </Link>
          ) : null}
          <Link href="/reports" className="secondary">
            Xem báo cáo <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </div>
        <div className="metric-grid" role="list" aria-label="Số liệu tổng quan">
          <div role="listitem">
            <MetricCard label="Tổng hồ sơ" value={workload.total} />
          </div>
          <div role="listitem">
            <MetricCard label="Đang review" value={count("IN_REVIEW")} />
          </div>
          <div role="listitem">
            <MetricCard label="Chờ Manager" value={count("PENDING_DECISION")} />
          </div>
          <div role="listitem">
            <MetricCard label="Đã quyết định" value={count("DECIDED")} />
          </div>
        </div>
        <div className="detail-grid">
          <section className="sheet" aria-label="Phân bố trạng thái review">
            <h2>Phân bố trạng thái review</h2>
            {workload.total ? (
              <CountChart data={workload.by_review_state} />
            ) : (
              <EmptyState>
                Chưa có hồ sơ. Manager có thể import catalog synthetic.
              </EmptyState>
            )}
          </section>
          <section className="sheet" aria-label="Model đang ghim">
            <p className="eyebrow">Model đang ghim</p>
            <p>
              <strong>{model?.model_version ?? "Chưa sẵn sàng"}</strong>
            </p>
            <p>
              <StatusBadge
                value="READY"
                label="ML prediction ≠ quyết định của con người"
                showCode={false}
              />
            </p>
            <p>
              Điểm số là tham khảo cho review; quyết định cuối thuộc về Manager
              và được ghi audit.
            </p>
            <Link href="/models">Mở Model Registry →</Link>
          </section>
        </div>
      </>
    );
  }
  return <EmptyState>Chưa có dữ liệu.</EmptyState>;
}

export function OverviewDashboard() {
  const { user } = useSession();
  return user ? <ResourcePage kind="overview" /> : null;
}
