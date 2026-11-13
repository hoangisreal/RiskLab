"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPage, mutate, uploadImport, messageFor } from "@/lib/api";
import { DataTable } from "@/components/data-table";
import type { ImportJob } from "@/lib/types";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

type Catalog = {
  version: string;
  feature_contract_version: string;
  snapshot_count: number;
};

export default function Imports() {
  const { user, pending } = useSession();
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const importIntent = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );

  useEffect(() => {
    if (user?.role !== "MANAGER") return;
    let cancelled = false;
    void Promise.all([
      api<Catalog[]>("/api/v1/demo-catalogs"),
      apiPage<ImportJob>("/api/v1/imports?page=1&page_size=20"),
    ])
      .then(([catalogResult, jobResult]) => {
        if (!cancelled) {
          setCatalogs(catalogResult);
          setJobs(jobResult.data);
          setTotal(jobResult.pagination.total);
          setError("");
        }
      })
      .catch((failure) => {
        if (!cancelled) setError(messageFor(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user, refreshKey]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const file = values.get("file");
    const catalog = String(values.get("catalog_version") ?? "");
    if (!(file instanceof File) || !catalog) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      // A lost response is ambiguous: retain the key for identical file bytes
      // and catalog until success, even if the user selects the file again.
      const digest = await crypto.subtle.digest(
        "SHA-256",
        await file.arrayBuffer(),
      );
      const fingerprint = `${catalog}:${Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("")}`;
      if (importIntent.current?.fingerprint !== fingerprint)
        importIntent.current = {
          fingerprint,
          key: `web-import-${crypto.randomUUID()}`,
        };
      const job = await uploadImport<ImportJob>(
        catalog,
        file,
        importIntent.current.key,
      );
      importIntent.current = null;
      setMessage(
        `Đã nhận import ${job.id.slice(0, 8)}… Trạng thái hiện tại: ${job.state}. Worker sẽ xử lý tiếp.`,
      );
      setRefreshKey((value) => value + 1);
    } catch (failure) {
      setError(messageFor(failure));
    } finally {
      setBusy(false);
    }
  }

  async function retry(job: ImportJob) {
    const reason = window.prompt("Lý do thử lại import?")?.trim();
    if (!reason) return;
    setBusy(true);
    setError("");
    try {
      await mutate(`/api/v1/imports/${job.id}/retry`, {
        expected_revision: job.revision,
        reason,
      });
      setMessage("Đã xếp hàng thử lại import.");
      setRefreshKey((value) => value + 1);
    } catch (failure) {
      setError(messageFor(failure));
    } finally {
      setBusy(false);
    }
  }

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
  if (user.role !== "MANAGER")
    return (
      <>
        <h1>Không có quyền import</h1>
        <p>Chỉ Manager mới import hồ sơ demo.</p>
      </>
    );

  return (
    <>
      <div className="page-heading">
        <h1>Import hồ sơ demo</h1>
        <p>
          CSV chỉ tham chiếu snapshot synthetic trong catalog đã duyệt. File lỗi
          không tạo hồ sơ một phần; hồ sơ thiếu nguồn bắt buộc sẽ ở trạng thái
          INCOMPLETE và không được chấm điểm.
        </p>
      </div>
      <section className="sheet" aria-labelledby="import-form-heading">
        <h2 id="import-form-heading">Tạo import mới</h2>
        {catalogs.length === 0 && !loading ? (
          <EmptyState>
            Chưa cấu hình catalog demo trên máy chủ. Liên hệ người vận hành.
          </EmptyState>
        ) : (
          <form className="form-sheet" onSubmit={submit}>
            <div className="field">
              <label htmlFor="catalog-version">Catalog demo</label>
              <select id="catalog-version" name="catalog_version" required>
                {catalogs.map((catalog) => (
                  <option key={catalog.version} value={catalog.version}>
                    {catalog.version} · contract{" "}
                    {catalog.feature_contract_version} ·{" "}
                    {catalog.snapshot_count} snapshot
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="import-file">Tệp CSV import</label>
              <input
                id="import-file"
                name="file"
                type="file"
                accept=".csv,text/csv"
                required
              />
            </div>
            <button type="submit" disabled={busy}>
              Import hồ sơ
            </button>
          </form>
        )}
        {message && (
          <p className="notice" role="status">
            {message}
          </p>
        )}
        {error && <ErrorState>{error}</ErrorState>}
      </section>
      <section
        className="sheet section-gap"
        aria-labelledby="import-jobs-heading"
      >
        <h2 id="import-jobs-heading">Các import gần đây</h2>
        <button onClick={() => setRefreshKey((value) => value + 1)}>
          Làm mới trạng thái
        </button>
        {loading ? (
          <LoadingState />
        ) : jobs.length === 0 ? (
          <EmptyState>Chưa có import nào. Tổng: {total}.</EmptyState>
        ) : (
          <>
            <div className="filterbar section-gap">
              <div className="field">
                <label htmlFor="import-search">
                  Tìm trong 20 import gần nhất
                </label>
                <input
                  id="import-search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="import-state">Trạng thái import</label>
                <select
                  id="import-state"
                  value={stateFilter}
                  onChange={(e) => setStateFilter(e.target.value)}
                >
                  <option value="">Tất cả</option>
                  {["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </div>
            </div>
            <DataTable
              data={jobs.filter(
                (j) =>
                  (!stateFilter || j.state === stateFilter) &&
                  `${j.id} ${j.catalog_version}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
              )}
              columns={[
                {
                  accessorKey: "id",
                  header: "Import",
                  cell: ({ row }) => (
                    <span>
                      {row.original.id.slice(0, 8)}… ·{" "}
                      {row.original.catalog_version}
                    </span>
                  ),
                },
                { accessorKey: "state", header: "Trạng thái" },
                {
                  accessorKey: "created_count",
                  header: "Kết quả",
                  cell: ({ row }) => (
                    <span>
                      {row.original.created_count}/
                      {row.original.total_rows ?? "—"} hồ sơ · READY{" "}
                      {row.original.ready_count} · INCOMPLETE{" "}
                      {row.original.incomplete_count}
                    </span>
                  ),
                },
                {
                  id: "errors",
                  header: "Lỗi",
                  cell: ({ row }) =>
                    row.original.errors
                      .map((e) => `${e.code} (dòng ${e.row ?? "—"})`)
                      .join("; ") || "—",
                },
                {
                  id: "actions",
                  header: "Thao tác",
                  cell: ({ row }) =>
                    row.original.state === "FAILED" &&
                    row.original.retryable ? (
                      <button
                        disabled={busy}
                        onClick={() => void retry(row.original)}
                      >
                        Thử lại import
                      </button>
                    ) : (
                      "—"
                    ),
                },
              ]}
            />
          </>
        )}
      </section>
    </>
  );
}
