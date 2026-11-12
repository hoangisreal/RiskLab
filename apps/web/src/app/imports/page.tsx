"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, apiPage, mutate, uploadImport, messageFor } from "@/lib/api";
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
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

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
      const job = await uploadImport<ImportJob>(
        catalog,
        file,
        `web-import-${crypto.randomUUID()}`,
      );
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
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Import</th>
                  <th>Trạng thái</th>
                  <th>Kết quả</th>
                  <th>Lỗi</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td data-label="Import">
                      {job.id.slice(0, 8)}… · {job.catalog_version}
                    </td>
                    <td data-label="Trạng thái">
                      {job.state} · lần {job.attempt}
                    </td>
                    <td data-label="Kết quả">
                      {job.created_count}/{job.total_rows ?? "—"} hồ sơ · READY{" "}
                      {job.ready_count} · INCOMPLETE {job.incomplete_count}
                    </td>
                    <td data-label="Lỗi">
                      {job.errors.length === 0
                        ? "—"
                        : job.errors
                            .map(
                              (item) =>
                                `${item.code} (dòng ${item.row ?? "—"})`,
                            )
                            .join("; ")}
                    </td>
                    <td data-label="Thao tác">
                      {job.state === "FAILED" && job.retryable ? (
                        <button disabled={busy} onClick={() => void retry(job)}>
                          Thử lại import
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
