"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, messageFor } from "@/lib/api";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

type Workload = {
  total: number;
  by_review_state: { value: string | null; count: number }[];
  by_scoring_state: { value: string | null; count: number }[];
  by_assignee: { value: string | null; count: number }[];
};
type CurrentModel = {
  model_version: string;
  model_sha256: string;
  feature_contract_version: string;
  model_run_id: string;
  lock_id: string;
  limitations: string[];
};
type Comparison = {
  report_version?: string;
  models?: {
    family: string;
    model_run_id: string;
    partition_role: string;
    metrics: Record<string, number>;
  }[];
  limitations: string[];
};

function CountTable({
  title,
  rows,
}: {
  title: string;
  rows: { value: string | null; count: number }[];
}) {
  return (
    <>
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <EmptyState>Chưa có dữ liệu.</EmptyState>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Giá trị</th>
                <th>Số lượng</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.value ?? "null"}>
                  <td data-label="Giá trị">{row.value ?? "Chưa phân công"}</td>
                  <td data-label="Số lượng">{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export default function Reports() {
  const { user, pending } = useSession();
  const [workload, setWorkload] = useState<Workload | null>(null);
  const [model, setModel] = useState<CurrentModel | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user || user.role === "ADMIN") return;
    let cancelled = false;
    void (async () => {
      try {
        setWorkload(await api<Workload>("/api/v1/reports/workload"));
        setModel(await api<CurrentModel>("/api/v1/models/current"));
        setComparison(
          await api<Comparison>("/api/v1/models/comparison").catch(() => null),
        );
        if (!cancelled) setError("");
      } catch (failure) {
        if (!cancelled) setError(messageFor(failure));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

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
        <h1>Không có quyền xem báo cáo nghiệp vụ</h1>
        <p>Admin chỉ quản lý tài khoản.</p>
      </>
    );
  if (loading) return <LoadingState />;
  if (!workload || !model)
    return <ErrorState>{error || "Không tải được báo cáo."}</ErrorState>;

  return (
    <>
      <div className="page-heading">
        <h1>Báo cáo cơ bản</h1>
        <p>
          Số liệu lấy từ dữ liệu đã lưu trong phạm vi quyền của bạn. Model hiển
          thị là bản đã ghim cho prediction; lịch sử quyết định luôn giữ model
          của prediction tại thời điểm review.
        </p>
      </div>
      <section className="sheet" aria-labelledby="workload-heading">
        <h2 id="workload-heading">Khối lượng hồ sơ ({workload.total})</h2>
        <CountTable
          title="Theo trạng thái review"
          rows={workload.by_review_state}
        />
        <CountTable
          title="Theo trạng thái scoring"
          rows={workload.by_scoring_state}
        />
        <CountTable title="Theo người phụ trách" rows={workload.by_assignee} />
      </section>
      <section className="sheet section-gap" aria-labelledby="model-heading">
        <h2 id="model-heading">Model hiện hành đã ghim</h2>
        <dl className="responsibilities">
          <div>
            <dt>Phiên bản</dt>
            <dd>{model.model_version}</dd>
          </div>
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
        <h3>Giới hạn bắt buộc</h3>
        <ul>
          {model.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
      <section
        className="sheet section-gap"
        aria-labelledby="comparison-heading"
      >
        <h2 id="comparison-heading">Báo cáo so sánh model</h2>
        {!comparison ? (
          <EmptyState>
            Chưa cấu hình báo cáo so sánh trên máy chủ; không hiển thị số liệu
            suy đoán.
          </EmptyState>
        ) : comparison.models && comparison.models.length > 0 ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Run</th>
                  <th>Phân vùng</th>
                  <th>Metrics</th>
                </tr>
              </thead>
              <tbody>
                {comparison.models.map((row) => (
                  <tr key={`${row.family}-${row.model_run_id}`}>
                    <td data-label="Model">{row.family}</td>
                    <td data-label="Run">{row.model_run_id}</td>
                    <td data-label="Phân vùng">{row.partition_role}</td>
                    <td data-label="Metrics">
                      {Object.entries(row.metrics)
                        .map(([name, value]) => `${name}: ${value}`)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>
            Báo cáo {comparison.report_version ?? ""} chưa có model.
          </EmptyState>
        )}
      </section>
    </>
  );
}
