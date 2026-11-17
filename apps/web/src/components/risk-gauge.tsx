"use client";

import { toPercent } from "@/lib/labels";
import { useMounted } from "@/lib/use-reduced-motion";

/**
 * Horizontal probability scale with the policy threshold as a fixed marker.
 * The fill is always indigo and the wording stays descriptive ("cao hơn /
 * thấp hơn ngưỡng"), because crossing the threshold is information for the
 * human reviewer, never an automatic decision.
 */
export function RiskGauge({
  probability,
  threshold,
  policyVersion,
}: {
  probability: number;
  threshold: number;
  policyVersion: string;
}) {
  const mounted = useMounted();
  const probabilityPct = Math.min(100, Math.max(0, probability * 100));
  const thresholdPct = Math.min(100, Math.max(0, threshold * 100));
  const deltaPoints = Math.abs(probability - threshold) * 100;
  const relation =
    probability >= threshold
      ? `cao hơn ngưỡng ${deltaPoints.toFixed(1)} điểm phần trăm`
      : `thấp hơn ngưỡng ${deltaPoints.toFixed(1)} điểm phần trăm`;
  return (
    <div className="risk-gauge">
      <div className="risk-gauge-head">
        <span className="risk-gauge-value">{toPercent(probability)}</span>
        <span className="risk-gauge-caption">
          Xác suất model · Ngưỡng {policyVersion}: {toPercent(threshold)} ·{" "}
          {relation}
        </span>
      </div>
      <div
        className="risk-track"
        role="img"
        aria-label={`Xác suất model ${toPercent(probability)}, ngưỡng chính sách ${toPercent(threshold)}, ${relation}`}
      >
        <div
          className="risk-fill"
          style={{ width: mounted ? `${probabilityPct}%` : "0%" }}
        />
        <div
          className="risk-threshold"
          style={{ left: `${thresholdPct}%` }}
          aria-hidden="true"
        />
      </div>
      <div className="risk-scale" aria-hidden="true">
        <span>0%</span>
        <span>100%</span>
      </div>
      <p className="risk-delta">
        Vị trí so với ngưỡng chỉ là thông tin tham khảo cho người review; model
        không tự tạo quyết định.
      </p>
    </div>
  );
}
