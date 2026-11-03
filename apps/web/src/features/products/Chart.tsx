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
import { TREND_COLORS } from "./chartColors";
export function Trend({ data, metrics }: { data: Board; metrics: string[] }) {
  const compare = metrics.length === 1 && (data.comparison_series?.length ?? 0) > 1;
  const lines = compare
    ? data.comparison_series.map((s, i) => ({
        key: s.listing_id,
        label: s.label,
        axis: metrics[0],
        color: TREND_COLORS[i % TREND_COLORS.length],
        type: data.metrics[metrics[0]].type,
      }))
    : metrics.map((k, i) => ({
        key: k,
        label: data.metrics[k].label,
        axis: k,
        color: TREND_COLORS[i % TREND_COLORS.length],
        type: data.metrics[k].type,
      }));
  const points: Record<string, string | number | null>[] = data.series.map((row, i) => ({
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
    <div className="m1-trend">
      <div role="img" aria-label={lines.map((k) => k.label).join("、") + "逐日趋势"}>
        <ResponsiveContainer width="100%" height={270} minWidth={0}>
          <LineChart data={points} margin={{ top: 24, right: 15, left: 8, bottom: 18 }}>
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
                        <i style={{ background: l.color }} />
                        {l.label}：
                        {format(
                          compare
                            ? data.comparison_series.find((s) => s.listing_id === l.key)?.points[
                                index
                              ]?.values[metrics[0]]
                            : row.values[l.key],
                          l.type,
                        )}
                        {!compare && row.partial?.includes(l.key) && "（已知部分）"}
                        {metrics.length === 1 && !compare && (
                          <>
                            <br />
                            <i style={{ background: "#9aa8ba" }} />
                            {row.previous_date === null
                              ? "去年无对应日期"
                              : `去年同期 ${row.previous_date ?? ""}`}
                            ：{format(row.previous[l.key], l.type)}
                            {row.previous_partial?.includes(l.key) && "（已知部分）"}
                          </>
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
                dot={({ cx, cy, index = 0 }) => {
                  const isolated =
                    points[index]?.[l.key] != null &&
                    points[index - 1]?.[l.key] == null &&
                    points[index + 1]?.[l.key] == null;
                  return isolated ? (
                    <circle key={index} cx={cx} cy={cy} r={3} fill={l.color} />
                  ) : (
                    <g key={index} />
                  );
                }}
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
                stroke="#9aa8ba"
                strokeWidth={2}
                dot={({ cx, cy, index = 0 }) => {
                  const key = `old_${metrics[0]}`;
                  const isolated =
                    points[index]?.[key] != null &&
                    points[index - 1]?.[key] == null &&
                    points[index + 1]?.[key] == null;
                  return isolated ? (
                    <circle key={index} cx={cx} cy={cy} r={3} fill="#9aa8ba" />
                  ) : (
                    <g key={index} />
                  );
                }}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!compare && metrics.length === 1 && (
        <details className="product-trend-basis">
          <summary>查看趋势口径</summary>
          <p>
            本期：{data.scope.start} 至 {data.scope.end}；去年同期：
            {data.comparison_periods?.yoy
              ? `${data.comparison_periods.yoy.start} 至 ${data.comparison_periods.yoy.end}`
              : "无对应日期"}
            。
          </p>
          <p>
            与上方指标卡一致：本期统计筛选后的商品；去年只计入这些商品在对应日期的记录。无记录不补0，部分数值不可用时会在悬浮提示中注明。数据来自数仓商品日报。
          </p>
        </details>
      )}
    </div>
  );
}
export function MiniTrend({ values }: { values: (string | null)[] }) {
  return (
    <div className="m1-mini" aria-label="近7日趋势">
      <ResponsiveContainer width="100%" height={28} minWidth={0}>
        <LineChart data={values.map((value) => ({ value: value == null ? null : Number(value) }))}>
          <Line
            dataKey="value"
            dot={false}
            stroke="#d84a4a"
            strokeWidth={2}
            isAnimationActive={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
