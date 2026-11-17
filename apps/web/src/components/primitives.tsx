"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  MinusCircle,
  XCircle,
} from "lucide-react";
import { usePrefersReducedMotion } from "@/lib/use-reduced-motion";

function CountingNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0);
  const frame = useRef<number>(0);
  useEffect(() => {
    const start = performance.now();
    const duration = 750;
    function tick(now: number) {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(eased * value));
      if (progress < 1) frame.current = requestAnimationFrame(tick);
    }
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [value]);
  return <>{display.toLocaleString("vi-VN")}</>;
}

/** Counts from 0 to `value` once visible; renders instantly if reduced motion. */
function AnimatedNumber({ value }: { value: number }) {
  const reduced = usePrefersReducedMotion();
  if (reduced) return <>{value.toLocaleString("vi-VN")}</>;
  return <CountingNumber value={value} />;
}

/** Shared page heading: title, Vietnamese summary and optional actions. */
export function PageHeader({
  title,
  intro,
  actions,
}: {
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="page-heading-row">
        <h1>{title}</h1>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {intro ? <p>{intro}</p> : null}
    </div>
  );
}

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

function toneFor(value: string): Tone {
  if (
    value === "SUCCEEDED" ||
    value === "DECIDED" ||
    value === "READY" ||
    value === "APPROVED_SIMULATED" ||
    value === "APPROVE"
  )
    return "success";
  if (
    value === "FAILED" ||
    value === "INVALID" ||
    value === "DECLINED_SIMULATED" ||
    value === "DECLINE"
  )
    return "danger";
  if (
    value === "RUNNING" ||
    value === "IN_REVIEW" ||
    value === "PENDING_DECISION" ||
    value === "QUEUED"
  )
    return "info";
  if (
    value === "ASSIGNED" ||
    value === "NOT_REQUESTED" ||
    value === "INCOMPLETE"
  )
    return "warning";
  return "neutral";
}

function ToneIcon({ tone }: { tone: Tone }) {
  const size = 13;
  if (tone === "success")
    return <CheckCircle2 size={size} aria-hidden="true" />;
  if (tone === "danger") return <XCircle size={size} aria-hidden="true" />;
  if (tone === "info") return <Loader2 size={size} aria-hidden="true" />;
  if (tone === "warning") return <Clock size={size} aria-hidden="true" />;
  return <MinusCircle size={size} aria-hidden="true" />;
}

/**
 * Status badge with an icon and Vietnamese label. The raw enum stays in the
 * title attribute and beside the label so automated checks keep working.
 */
export function StatusBadge({
  value,
  label,
  showCode = true,
}: {
  value: string;
  label: string;
  showCode?: boolean;
}) {
  const tone = toneFor(value);
  return (
    <span className={`status-badge tone-${tone}`} title={value}>
      <ToneIcon tone={tone} />
      <span>{label}</span>
      {showCode && label !== value ? (
        <code className="status-code">{value}</code>
      ) : null}
    </span>
  );
}

/** Metric card for overview dashboards. */
export function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="metric">
      <small>{label}</small>
      <strong>
        {typeof value === "number" ? <AnimatedNumber value={value} /> : value}
      </strong>
      {hint ? <span className="metric-hint">{hint}</span> : null}
    </div>
  );
}

/** Skeleton loading block that mirrors the layout being loaded. */
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton-line" />
      ))}
    </div>
  );
}

/** Inline alert for form-level errors. */
export function AlertIcon() {
  return <AlertCircle size={14} aria-hidden="true" />;
}
