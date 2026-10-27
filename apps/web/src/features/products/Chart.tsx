import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Board } from "./contracts";
import { format, type Kind } from "./format";
const COLORS = ["#1768d9", "#d97932", "#1b9878", "#a46bb1", "#d84a4a", "#30a5c0", "#a28942"];
export function Trend({ data, metrics }: { data: Board; metrics: string[] }) {
  const compare = metrics.length === 1 && (data.comparison_series?.length ?? 0) > 1;
  const lines = compare
    ? data.comparison_series.map((s, i) => ({
        key: s.listing_id,
        label: s.label,
        axis: metrics[0],
        color: COLORS[i % COLORS.length],
        type: data.metrics[metrics[0]].type,
      }))
    : metrics.map((k, i) => ({
        key: k,
        label: data.metrics[k].label,
        axis: k,
        color: COLORS[i],
        type: data.metrics[k].type,
      }));
  const points = data.series.map((row, i) => ({
    date: row.date,
    ...Object.fromEntries(
      compare
        ? data.comparison_series.map((s) => [
            s.listing_id,
            s.points[i]?.values[metrics[0]] == null ? null : Number(s.points[i].values[metrics[0]]),
          ])
        : metrics.flatMap((k) => [
            [k, row.values[k] == null ? null : Number(row.values[k])],
            [`old_${k}`, row.previous[k] == null ? null : Number(row.previous[k])],
          ]),
    ),
  }));
  return (
    <div
      className="m1-trend"
      role="img"
      aria-label={lines.map((k) => k.label).join("、") + "逐日趋势"}
    >
      <ResponsiveContainer width="100%" height={285} minWidth={0}>
        <LineChart data={points} margin={{ top: 22, right: 15, left: 8, bottom: 8 }}>
          <CartesianGrid vertical={false} stroke="#e4ebf3" />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => String(v).slice(5)}
            minTickGap={38}
            axisLine={false}
            tickLine={false}
            fontSize={11}
          />
          {metrics.map((k, i) => (
            <YAxis
              key={k}
              yAxisId={k}
              hide={i > 0}
              width={68}
              axisLine={false}
              tickLine={false}
              fontSize={11}
              tickFormatter={(v) => format(String(v), data.metrics[k].type as Kind)}
            />
          ))}
          <Tooltip
            content={({ active, label }) => {
              if (!active) return null;
              const index = data.series.findIndex((r) => r.date === label);
              const row = data.series[index];
              return row ? (
                <div className="m1-chart-tooltip">
                  <b>{row.date}</b>
                  {lines.map((l) => (
                    <p key={l.key}>
                      {l.label}：
                      {format(
                        compare
                          ? data.comparison_series.find((s) => s.listing_id === l.key)?.points[
                              index
                            ]?.values[metrics[0]]
                          : row.values[l.key],
                        l.type,
                      )}
                      {metrics.length === 1 && !compare && (
                        <> · 去年 {format(row.previous[l.key], l.type)}</>
                      )}
                    </p>
                  ))}
                </div>
              ) : null;
            }}
          />
          {lines.map((l) => (
            <Line
              key={l.key}
              type="linear"
              dataKey={l.key}
              yAxisId={l.axis}
              name={l.label}
              stroke={l.color}
              dot={false}
              strokeWidth={2.5}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
          {metrics.length === 1 && !compare && (
            <Line
              type="linear"
              dataKey={`old_${metrics[0]}`}
              yAxisId={metrics[0]}
              stroke="#8294a8"
              strokeDasharray="6 5"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
export function MiniTrend({ values }: { values: (string | null)[] }) {
  return (
    <div className="m1-mini" aria-label="近7日趋势">
      <ResponsiveContainer width="100%" height={30} minWidth={0}>
        <LineChart data={values.map((value) => ({ value: value == null ? null : Number(value) }))}>
          <Line
            dataKey="value"
            dot={false}
            stroke="#d46054"
            strokeWidth={1.7}
            isAnimationActive={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
