"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiPage, messageFor } from "@/lib/api";
import type { ApplicationSummary } from "@/lib/types";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

export default function Applications() {
  const { user, pending } = useSession();
  const [rows, setRows] = useState<ApplicationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [reviewState, setReviewState] = useState("");
  const [scoringState, setScoringState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!user || user.role === "ADMIN") return;
    const controller = new AbortController();
    const params = new URLSearchParams({ page: String(page), page_size: "20" });
    if (query) params.set("q", query);
    if (reviewState) params.set("review_state", reviewState);
    if (scoringState) params.set("scoring_state", scoringState);
    void apiPage<ApplicationSummary>(`/api/v1/applications?${params}`)
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
  }, [user, page, query, reviewState, scoringState, refreshKey]);

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

  return (
    <>
      <div className="page-heading">
        <h1>Hồ sơ</h1>
        <p>
          {user.role === "ANALYST"
            ? "Chỉ hiển thị hồ sơ được phân công cho bạn."
            : "Toàn bộ hồ sơ demo trong phạm vi Manager."}{" "}
          Điểm số là hỗ trợ quyết định mô phỏng, không phải phán quyết tự động.
        </p>
      </div>
      <section className="sheet" aria-labelledby="applications-heading">
        <h2 id="applications-heading">Danh sách hồ sơ</h2>
        <form
          className="filterbar"
          onSubmit={(event) => {
            event.preventDefault();
            setPage(1);
            setRefreshKey((value) => value + 1);
          }}
        >
          <div className="field">
            <label htmlFor="filter-q">Mã hồ sơ</label>
            <input
              id="filter-q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={64}
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
          <button type="submit">Lọc hồ sơ</button>
        </form>
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState retry={() => setRefreshKey((value) => value + 1)}>
            {error}
          </ErrorState>
        ) : rows.length === 0 ? (
          <EmptyState>Chưa có hồ sơ phù hợp.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Mã hồ sơ</th>
                  <th>Người phụ trách</th>
                  <th>Nguồn dữ liệu</th>
                  <th>Scoring</th>
                  <th>Review</th>
                  <th>Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td data-label="Mã hồ sơ">{row.external_reference}</td>
                    <td data-label="Người phụ trách">
                      {row.assignee?.username ?? "—"}
                    </td>
                    <td data-label="Nguồn dữ liệu">
                      {row.feature_availability}
                    </td>
                    <td data-label="Scoring">{row.scoring_state}</td>
                    <td data-label="Review">{row.review_state}</td>
                    <td data-label="Chi tiết">
                      <Link href={`/applications/${row.id}`}>Xem hồ sơ</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Trang trước
          </button>
          <span>
            Trang {page} · Tổng {total} hồ sơ
          </span>
          <button disabled={rows.length < 20} onClick={() => setPage(page + 1)}>
            Trang sau
          </button>
        </div>
      </section>
    </>
  );
}
