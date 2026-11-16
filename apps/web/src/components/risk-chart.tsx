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
import { labelOf, REVIEW_STATE_LABEL } from "@/lib/labels";
export function CountChart({
  data,
}: {
  data: { value: string; count: number }[];
}) {
  const localized = data.map((d) => ({
    ...d,
    label: labelOf(REVIEW_STATE_LABEL, d.value),
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
          <Tooltip />
          <Bar
            dataKey="count"
            name="Số hồ sơ"
            fill="#4f46e5"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
