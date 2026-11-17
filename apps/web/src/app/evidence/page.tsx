"use client";

import Link from "next/link";
import { useSession } from "@/components/session";
import { EmptyState, LoadingState } from "@/components/states";
import { MetricCard, PageHeader } from "@/components/primitives";
import { ModelAucChart } from "@/components/risk-chart";
import {
  E7_COMPARISON,
  E8_TEMPORAL,
  EVIDENCE_LIMITATIONS,
  LOCKED_EVALUATION,
} from "@/lib/evidence";

/** One metric with its 95% CI drawn on a padded per-metric scale. */
function MetricInterval({
  name,
  value,
  low,
  high,
}: {
  name: string;
  value: number;
  low: number;
  high: number;
}) {
  const span = Math.max(high - low, 1e-9);
  const min = low - span * 0.6;
  const max = high + span * 0.6;
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  return (
    <div className="ci-row">
      <span>{name}</span>
      <span
        className="ci-track"
        role="img"
        aria-label={`${name}: ${value.toFixed(4)}, khoảng tin cậy 95% từ ${low.toFixed(4)} đến ${high.toFixed(4)}`}
      >
        <span
          className="ci-range"
          style={{ left: `${pct(low)}%`, width: `${pct(high) - pct(low)}%` }}
        />
        <span className="ci-point" style={{ left: `${pct(value)}%` }} />
      </span>
      <span className="ci-value">
        {value.toFixed(4)} · [{low.toFixed(4)}; {high.toFixed(4)}]
      </span>
    </div>
  );
}

export default function Evidence() {
  const { user, pending } = useSession();
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
        <h1>Không có quyền xem bằng chứng ML</h1>
        <p>Admin chỉ quản lý tài khoản.</p>
      </>
    );
  return (
    <>
      <PageHeader
        title="Bằng chứng ML lịch sử"
        intro="Kết quả nghiên cứu đã khóa trên dữ liệu Home Credit thật, trích từ docs/RESULTS.md và docs/portfolio. Đây không phải metrics của model synthetic đang chấm điểm trong demo này."
      />
      <p className="notice" role="status">
        Cảnh báo phạm vi: các số dưới đây thuộc artifact nghiên cứu đã đánh giá
        (run {LOCKED_EVALUATION.runId.slice(0, 8)}…, lock{" "}
        {LOCKED_EVALUATION.lockId}). Model trong workspace này là bản synthetic
        phục vụ kiểm tra workflow và không kế thừa các metrics này.
      </p>

      <section
        className="sheet section-gap reveal"
        aria-labelledby="locked-heading"
      >
        <h2 id="locked-heading">
          Đánh giá Test đã khóa — {LOCKED_EVALUATION.model}
        </h2>
        <div className="metric-grid" role="list" aria-label="Thông tin lock">
          <div role="listitem">
            <MetricCard
              label="Hồ sơ Test"
              value={String(LOCKED_EVALUATION.testApplicants)}
            />
          </div>
          <div role="listitem">
            <MetricCard
              label="Ngưỡng chính sách"
              value={LOCKED_EVALUATION.threshold.toFixed(4)}
            />
          </div>
          <div role="listitem">
            <MetricCard label="Lock" value={LOCKED_EVALUATION.lockId} />
          </div>
          <div role="listitem">
            <MetricCard
              label="Run"
              value={`${LOCKED_EVALUATION.runId.slice(0, 8)}…`}
            />
          </div>
        </div>
        {LOCKED_EVALUATION.metrics.map((metric) => (
          <MetricInterval key={metric.name} {...metric} />
        ))}
        <p>{LOCKED_EVALUATION.note}</p>
      </section>

      <section
        className="sheet section-gap reveal reveal-delay-1"
        aria-labelledby="e7-heading"
      >
        <h2 id="e7-heading">So sánh 12 pipeline (E7, Selection)</h2>
        <p>{E7_COMPARISON.note}</p>
        <ModelAucChart models={[...E7_COMPARISON.models]} />
      </section>

      <section
        className="sheet section-gap reveal reveal-delay-2"
        aria-labelledby="e8-heading"
      >
        <h2 id="e8-heading">Benchmark theo thời gian (E8)</h2>
        <p>{E8_TEMPORAL.note}</p>
        {E8_TEMPORAL.models.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>ROC-AUC</th>
                  <th>PR-AUC</th>
                </tr>
              </thead>
              <tbody>
                {E8_TEMPORAL.models.map((model) => (
                  <tr key={model.family}>
                    <td data-label="Model">{model.family}</td>
                    <td data-label="ROC-AUC">{model.auc.toFixed(4)}</td>
                    <td data-label="PR-AUC">{model.prAuc.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>Chưa có dữ liệu.</EmptyState>
        )}
      </section>

      <section className="sheet section-gap" aria-labelledby="limits-heading">
        <h2 id="limits-heading">Giới hạn bắt buộc</h2>
        <ul>
          {EVIDENCE_LIMITATIONS.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p>
          Nguồn canonical: <code>docs/RESULTS.md</code>,{" "}
          <code>docs/model_card.md</code>,{" "}
          <code>docs/portfolio/model-comparison.md</code>,{" "}
          <code>docs/portfolio/temporal-validation.md</code>.
        </p>
      </section>
    </>
  );
}
