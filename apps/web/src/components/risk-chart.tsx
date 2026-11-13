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
export function CountChart({
  data,
}: {
  data: { value: string; count: number }[];
}) {
  return (
    <div
      className="chart-frame"
      role="img"
      aria-label={data.map((d) => `${d.value}: ${d.count}`).join(", ")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} accessibilityLayer>
          <CartesianGrid vertical={false} stroke="#eceef4" />
          <XAxis dataKey="value" tick={{ fontSize: 10 }} interval={0} />
          <YAxis allowDecimals={false} width={35} />
          <Tooltip />
          <Bar
            dataKey="count"
            name="Hồ sơ"
            fill="#7065df"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
