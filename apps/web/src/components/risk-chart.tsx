"use client";
import {
  BarChart,
  Bar,
  ResponsiveContainer,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { labelOf, REVIEW_STATE_LABEL, SCORING_STATE_LABEL } from "@/lib/labels";
import { useMounted, usePrefersReducedMotion } from "@/lib/use-reduced-motion";

const TOOLTIP_STYLE = {
  border: "1px solid #e4e7ef",
  borderRadius: 8,
  fontSize: 12,
  boxShadow: "0 4px 16px rgb(24 32 50 / 0.08)",
} as const;

export function CountChart({
  data,
  labels = REVIEW_STATE_LABEL,
}: {
  data: { value: string | null; count: number }[];
  labels?: Record<string, string>;
}) {
  const reduced = usePrefersReducedMotion();
  const localized = data.map((d) => ({
    ...d,
    label: labelOf(labels, d.value),
  }));
  return (
    <div
      className="chart-frame"
      role="img"
      aria-label={localized.map((d) => `${d.label}: ${d.count}`).join(", ")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={localized} accessibilityLayer>
          <CartesianGrid vertical={false} stroke="#e4e7ef" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} />
          <YAxis allowDecimals={false} width={35} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f4f5f9" }} />
          <Bar
            dataKey="count"
            name="Số hồ sơ"
            fill="#4f46e5"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
            isAnimationActive={!reduced}
            animationDuration={700}
            animationEasing="ease-out"
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ScoringChart({
  data,
}: {
  data: { value: string; count: number }[];
}) {
  return <CountChart data={data} labels={SCORING_STATE_LABEL} />;
}

/** Horizontal bars for workload per assignee; the name is the category. */
export function AssigneeChart({
  data,
}: {
  data: { value: string | null; count: number }[];
}) {
  const reduced = usePrefersReducedMotion();
  const localized = data.map((d) => ({
    label: d.value ?? "Chưa phân công",
    count: d.count,
  }));
  return (
    <div
      className="chart-frame"
      role="img"
      aria-label={localized.map((d) => `${d.label}: ${d.count}`).join(", ")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={localized} layout="vertical" accessibilityLayer>
          <CartesianGrid horizontal={false} stroke="#e4e7ef" />
          <XAxis type="number" allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fontSize: 11 }}
            width={130}
          />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f4f5f9" }} />
          <Bar
            dataKey="count"
            name="Số hồ sơ"
            fill="#4f46e5"
            radius={[0, 4, 4, 0]}
            maxBarSize={28}
            isAnimationActive={!reduced}
            animationDuration={700}
            animationEasing="ease-out"
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Horizontal ROC-AUC bars for the historical E7 comparison. Every family
 * keeps the same indigo; identity comes from the row label, never from a
 * rank-based recoloring.
 */
export function ModelAucChart({
  models,
}: {
  models: { family: string; auc: number }[];
}) {
  const reduced = usePrefersReducedMotion();
  return (
    <div
      style={{ width: "100%", minWidth: 0, height: 380 }}
      role="img"
      aria-label={models
        .map((m) => `${m.family}: ${m.auc.toFixed(4)}`)
        .join(", ")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={[...models]} layout="vertical" accessibilityLayer>
          <CartesianGrid horizontal={false} stroke="#e4e7ef" />
          <XAxis type="number" domain={[0.5, 0.8]} tick={{ fontSize: 11 }} />
          <YAxis
            type="category"
            dataKey="family"
            tick={{ fontSize: 11 }}
            width={170}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{ fill: "#f4f5f9" }}
            formatter={(value) => [
              typeof value === "number" ? value.toFixed(4) : value,
              "ROC-AUC",
            ]}
          />
          <Bar
            dataKey="auc"
            name="ROC-AUC"
            fill="#4f46e5"
            radius={[0, 4, 4, 0]}
            maxBarSize={22}
            isAnimationActive={!reduced}
            animationDuration={700}
            animationEasing="ease-out"
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Ten fixed probability buckets from the risk-distribution endpoint.
 * Counts are aggregates over scored cases in the caller's scope only.
 */
export function RiskDistributionChart({
  buckets,
}: {
  buckets: { lower: number; upper: number; count: number }[];
}) {
  const reduced = usePrefersReducedMotion();
  const localized = buckets.map((b) => ({
    label: `${b.lower.toFixed(1)}–${b.upper.toFixed(1)}`,
    count: b.count,
  }));
  return (
    <div
      className="chart-frame"
      role="img"
      aria-label={localized.map((d) => `${d.label}: ${d.count}`).join(", ")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={localized} accessibilityLayer>
          <CartesianGrid vertical={false} stroke="#e4e7ef" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} />
          <YAxis allowDecimals={false} width={35} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f4f5f9" }} />
          <Bar
            dataKey="count"
            name="Số hồ sơ"
            fill="#4f46e5"
            radius={[4, 4, 0, 0]}
            maxBarSize={36}
            isAnimationActive={!reduced}
            animationDuration={700}
            animationEasing="ease-out"
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Fixed workflow order; shades run light to dark as a case advances. */
const FUNNEL_ORDER = [
  "UNASSIGNED",
  "ASSIGNED",
  "IN_REVIEW",
  "PENDING_DECISION",
  "DECIDED",
] as const;
const FUNNEL_SHADES = ["#c7d2fe", "#a5b4fc", "#818cf8", "#6366f1", "#4338ca"];

/**
 * Single segmented bar showing how the workload is distributed across the
 * review workflow. The legend carries every label, count and share, so the
 * bar is never the only way to read the data.
 */
export function WorkflowFunnel({
  data,
  total,
}: {
  data: { value: string | null; count: number }[];
  total: number;
}) {
  const mounted = useMounted();
  const counts = new Map(data.map((d) => [d.value, d.count]));
  const segments = FUNNEL_ORDER.map((value, index) => ({
    value,
    label: labelOf(REVIEW_STATE_LABEL, value),
    count: counts.get(value) ?? 0,
    shade: FUNNEL_SHADES[index],
  })).filter((segment) => segment.count > 0);
  const summary = segments
    .map(
      (s) =>
        `${s.label}: ${s.count} (${Math.round((s.count / Math.max(1, total)) * 100)}%)`,
    )
    .join(", ");
  return (
    <div className="funnel">
      <div className="funnel-track" role="img" aria-label={summary}>
        {segments.map((segment) => (
          <div
            key={segment.value}
            className="funnel-segment"
            style={{
              width: mounted
                ? `${(segment.count / Math.max(1, total)) * 100}%`
                : "0%",
              background: segment.shade,
            }}
          />
        ))}
      </div>
      <ul className="funnel-legend">
        {segments.map((segment) => (
          <li key={segment.value}>
            <span
              className="funnel-dot"
              style={{ background: segment.shade }}
              aria-hidden="true"
            />
            <span>{segment.label}</span>
            <strong>
              {segment.count} ·{" "}
              {Math.round((segment.count / Math.max(1, total)) * 100)}%
            </strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
