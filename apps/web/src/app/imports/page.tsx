"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPage, mutate, uploadImport, messageFor } from "@/lib/api";
import { DataTable } from "@/components/data-table";
import type { ImportJob } from "@/lib/types";
import { IMPORT_STATE_LABEL, labelOf } from "@/lib/labels";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PageHeader, StatusBadge } from "@/components/primitives";

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
  const [fileMeta, setFileMeta] = useState("");
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
        `Đã nhận import ${job.id.slice(0, 8)}… Trạng thái hiện tại: ${labelOf(IMPORT_STATE_LABEL, job.state)}. Worker sẽ xử lý tiếp.`,
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

  const visible = jobs.filter(
    (j) =>
      (!stateFilter || j.state === stateFilter) &&
      `${j.id} ${j.catalog_version}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );

  return (
    <>
      <PageHeader
        title="Import hồ sơ demo"
        intro="CSV chỉ tham chiếu snapshot synthetic trong catalog đã duyệt. File lỗi không tạo hồ sơ một phần; hồ sơ thiếu nguồn bắt buộc sẽ ở trạng thái thiếu nguồn và không được chấm điểm."
      />
      <section className="sheet" aria-labelledby="import-form-heading">
        <h2 id="import-form-heading">Tạo import mới</h2>
        <ol className="steps-list" aria-label="Ba bước import">
          <li>Chọn catalog demo và tệp CSV đúng định dạng.</li>
          <li>Gửi file; giữ nguyên thao tác nếu mất kết nối giữa chừng.</li>
          <li>Theo dõi trạng thái xử lý ở danh sách bên dưới.</li>
        </ol>
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
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  setFileMeta(
                    file
                      ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB`
                      : "",
                  );
                }}
              />
              {fileMeta ? (
                <p className="file-meta" role="status">
                  Đã chọn: {fileMeta}
                </p>
              ) : null}
            </div>
            <button type="submit" className="btn-primary" disabled={busy}>
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
                    <option key={x} value={x}>
                      {labelOf(IMPORT_STATE_LABEL, x)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className="result-count" role="status">
              Hiển thị {visible.length} / {jobs.length} import
            </p>
            <DataTable
              data={visible}
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
                {
                  accessorKey: "state",
                  header: "Trạng thái",
                  cell: ({ row }) => (
                    <StatusBadge
                      value={row.original.state}
                      label={labelOf(IMPORT_STATE_LABEL, row.original.state)}
                    />
                  ),
                },
                {
                  accessorKey: "created_count",
                  header: "Kết quả",
                  cell: ({ row }) => (
                    <span>
                      {row.original.created_count}/
                      {row.original.total_rows ?? "—"} hồ sơ · Sẵn sàng{" "}
                      {row.original.ready_count} · Thiếu nguồn{" "}
                      {row.original.incomplete_count}
                    </span>
                  ),
                },
                {
                  id: "errors",
                  header: "Lỗi theo dòng",
                  cell: ({ row }) =>
                    row.original.errors.length > 0 ? (
                      <ul>
                        {row.original.errors.map((e, i) => (
                          <li key={i}>
                            {e.code}
                            {e.row != null ? ` (dòng ${e.row})` : ""}
                            {e.field ? ` · ${e.field}` : ""}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      "—"
                    ),
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
