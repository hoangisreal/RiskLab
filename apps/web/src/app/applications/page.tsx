"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { DataTable } from "@/components/data-table";
import { apiPage, messageFor } from "@/lib/api";
import type { ApplicationSummary } from "@/lib/types";
import {
  FEATURE_AVAILABILITY_LABEL,
  REVIEW_STATE_LABEL,
  SCORING_STATE_LABEL,
  labelOf,
} from "@/lib/labels";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PageHeader, StatusBadge } from "@/components/primitives";

const PAGE_SIZE = 20;

function queueDefault(role: string): string {
  return role === "MANAGER" ? "PENDING_DECISION" : "ASSIGNED";
}

export default function Applications({ queue = false }: { queue?: boolean }) {
  return (
    <Suspense fallback={<LoadingState />}>
      <ApplicationsInner queue={queue} />
    </Suspense>
  );
}

function ApplicationsInner({ queue = false }: { queue?: boolean }) {
  const { user, pending } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [rows, setRows] = useState<ApplicationSummary[]>([]);
  const [total, setTotal] = useState(0);
  // Draft inputs; applied only on submit so typing never fires requests.
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [reviewState, setReviewState] = useState(
    params.get("review_state") ?? (queue ? "QUEUE" : ""),
  );
  const [scoringState, setScoringState] = useState(
    params.get("scoring_state") ?? "",
  );
  const applied = {
    q: params.get("q") ?? "",
    review_state: params.get("review_state") ?? (queue ? "QUEUE" : ""),
    scoring_state: params.get("scoring_state") ?? "",
    page: Math.max(1, Number(params.get("page") ?? "1") || 1),
  };
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!user || user.role === "ADMIN") return;
    const controller = new AbortController();
    const search = new URLSearchParams({
      page: String(applied.page),
      page_size: String(PAGE_SIZE),
    });
    if (applied.q) search.set("q", applied.q);
    if (applied.review_state)
      search.set(
        "review_state",
        applied.review_state === "QUEUE"
          ? queueDefault(user.role)
          : applied.review_state,
      );
    if (applied.scoring_state)
      search.set("scoring_state", applied.scoring_state);
    void apiPage<ApplicationSummary>(`/api/v1/applications?${search}`)
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
  }, [
    user,
    applied.q,
    applied.review_state,
    applied.scoring_state,
    applied.page,
    refreshKey,
  ]);

  if (pending) return <LoadingState />;
  if (!user)
    return (
      <>
        <h1>Phiên đăng nhập không còn hiệu lực</h1>
        <p>
          <Link href="/login">Đăng nhập lại</Link>
        </p>
      </>
    );
  if (user.role === "ADMIN")
    return (
      <>
        <h1>Không có quyền truy cập hồ sơ</h1>
        <p>Admin quản lý tài khoản; quyền hồ sơ thuộc Analyst và Manager.</p>
      </>
    );

  function submitParams(next: URLSearchParams) {
    // Replacing with identical params fires no navigation, so refetch
    // explicitly instead of leaving the loading skeleton stuck on.
    setLoading(true);
    if (next.toString() === params.toString()) {
      setRefreshKey((value) => value + 1);
    } else {
      router.replace(`?${next.toString()}`);
    }
  }

  function applyFilters(event?: React.FormEvent) {
    event?.preventDefault();
    const next = new URLSearchParams();
    if (query.trim()) next.set("q", query.trim());
    if (reviewState) next.set("review_state", reviewState);
    if (scoringState) next.set("scoring_state", scoringState);
    next.set("page", "1");
    submitParams(next);
  }

  function clearFilters() {
    setQuery("");
    setReviewState(queue ? "QUEUE" : "");
    setScoringState("");
    submitParams(new URLSearchParams("page=1"));
  }

  function goTo(page: number) {
    const next = new URLSearchParams(params.toString());
    next.set("page", String(page));
    submitParams(next);
  }

  const hasFilter = Boolean(
    applied.q || applied.review_state || applied.scoring_state,
  );

  return (
    <>
      <PageHeader
        title={queue ? "Hàng đợi review" : "Hồ sơ"}
        intro={
          user.role === "ANALYST"
            ? "Chỉ hiển thị hồ sơ được phân công cho bạn."
            : "Toàn bộ hồ sơ demo trong phạm vi Manager."
        }
      />
      <section className="sheet" aria-labelledby="applications-heading">
        <h2 id="applications-heading">Danh sách hồ sơ</h2>
        <form className="filterbar" onSubmit={applyFilters}>
          <div className="field">
            <label htmlFor="filter-q">Mã hồ sơ</label>
            <input
              id="filter-q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={64}
              placeholder="VD: DEMO-001"
            />
          </div>
          <div className="field">
            <label htmlFor="filter-review">Trạng thái review</label>
            <select
              id="filter-review"
              value={reviewState}
              onChange={(event) => setReviewState(event.target.value)}
            >
              <option value="">Tất cả</option>
              {queue && (
                <option value="QUEUE">
                  {user.role === "MANAGER"
                    ? "Chờ quyết định của Manager"
                    : "Được giao, chưa bắt đầu"}
                </option>
              )}
              <option value="UNASSIGNED">Chưa phân công</option>
              <option value="ASSIGNED">Đã phân công</option>
              <option value="IN_REVIEW">Đang review</option>
              <option value="PENDING_DECISION">Chờ quyết định</option>
              <option value="DECIDED">Đã quyết định</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="filter-scoring">Trạng thái scoring</label>
            <select
              id="filter-scoring"
              value={scoringState}
              onChange={(event) => setScoringState(event.target.value)}
            >
              <option value="">Tất cả</option>
              <option value="NOT_REQUESTED">Chưa yêu cầu</option>
              <option value="QUEUED">Đang chờ</option>
              <option value="RUNNING">Đang chấm</option>
              <option value="SUCCEEDED">Đã có điểm</option>
              <option value="FAILED">Chấm điểm lỗi</option>
            </select>
          </div>
          <div className="filterbar-actions">
            <button type="submit" className="btn-primary">
              Lọc hồ sơ
            </button>
            {hasFilter ? (
              <button type="button" onClick={clearFilters}>
                Xóa bộ lọc
              </button>
            ) : null}
          </div>
        </form>
        <p className="result-count" role="status">
          Tổng {total} hồ sơ
          {hasFilter ? " khớp bộ lọc" : ""}
        </p>
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState retry={() => setRefreshKey((value) => value + 1)}>
            {error}
          </ErrorState>
        ) : rows.length === 0 ? (
          <EmptyState>
            {hasFilter
              ? "Không có hồ sơ khớp bộ lọc. Thử xóa bộ lọc hoặc tìm mã khác."
              : "Chưa có hồ sơ nào."}
          </EmptyState>
        ) : (
          <DataTable
            data={rows}
            columns={[
              { accessorKey: "external_reference", header: "Mã hồ sơ" },
              {
                id: "assignee",
                accessorFn: (row) => row.assignee?.username ?? "—",
                header: "Người phụ trách",
              },
              {
                accessorKey: "review_state",
                header: "Review",
                cell: ({ row }) => (
                  <StatusBadge
                    value={row.original.review_state}
                    label={labelOf(
                      REVIEW_STATE_LABEL,
                      row.original.review_state,
                    )}
                  />
                ),
              },
              {
                accessorKey: "scoring_state",
                header: "Scoring",
                cell: ({ row }) => (
                  <StatusBadge
                    value={row.original.scoring_state}
                    label={labelOf(
                      SCORING_STATE_LABEL,
                      row.original.scoring_state,
                    )}
                  />
                ),
              },
              {
                accessorKey: "feature_availability",
                header: "Dữ liệu",
                cell: ({ row }) => (
                  <StatusBadge
                    value={row.original.feature_availability}
                    label={labelOf(
                      FEATURE_AVAILABILITY_LABEL,
                      row.original.feature_availability,
                    )}
                  />
                ),
              },
              {
                id: "detail",
                header: "Thao tác",
                cell: ({ row }) => (
                  <Link href={`/applications/${row.original.id}`}>
                    Xem hồ sơ
                  </Link>
                ),
              },
            ]}
          />
        )}
        <div className="pagination">
          <button
            disabled={applied.page <= 1}
            onClick={() => goTo(applied.page - 1)}
          >
            Trang trước
          </button>
          <span>
            Trang {applied.page} · Tổng {total} hồ sơ
          </span>
          <button
            disabled={applied.page * PAGE_SIZE >= total}
            onClick={() => goTo(applied.page + 1)}
          >
            Trang sau
          </button>
        </div>
      </section>
    </>
  );
}
