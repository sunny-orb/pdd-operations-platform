import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Overview } from "./contracts";
import { formatValue, specs } from "./format";
import type { MetricKey } from "./state";

/** Number只用于图形坐标，tooltip读取响应原十进制字符串；缺日和NULL不连线。 */
export function Trend({ data, selected }: { data: Overview | undefined; selected: MetricKey[] }) {
  const series = data?.series ?? [];
  const chart = series.map((point) => ({
    ...point,
    ...Object.fromEntries(
      selected.flatMap((key) => [
        [
          `${key}_now`,
          point.missing || point.metrics[key]?.value == null
            ? null
            : Number(point.metrics[key].value),
        ],
        [
          `${key}_prior`,
          point.previous[key]?.value == null ? null : Number(point.previous[key].value),
        ],
      ]),
    ),
  }));
  const selectedSpecs = selected.map((key) => specs.find((s) => s.key === key)!);
  return (
    <>
      <div className="legend">
        {selectedSpecs.map((spec) => (
          <span key={spec.key}>
            <i style={{ background: spec.color }} />
            {spec.label}
          </span>
        ))}
        {series.some((p) => selected.some((key) => p.previous[key]?.value != null)) && (
          <span className="last-legend">
            <i />
            去年同期（虚线）
          </span>
        )}
      </div>
      <div
        className="m2-trend"
        role="img"
        aria-label="推广核心指标趋势图：实线本期，虚线去年同期；缺日断线"
      >
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chart} margin={{ top: 24, bottom: 10, left: 0, right: 0 }}>
            <CartesianGrid vertical={false} stroke="#e1e9f3" />
            <XAxis
              dataKey="date"
              tickFormatter={(value) => String(value).slice(5)}
              tick={{ fontSize: 11, fill: "#7b8a9b" }}
              minTickGap={30}
              tickLine={false}
              axisLine={false}
            />
            {selectedSpecs.map((spec, index) => (
              <YAxis
                key={spec.key}
                yAxisId={spec.key}
                hide={index > 1}
                orientation={index === 1 ? "right" : "left"}
                domain={["auto", "auto"]}
                width={84}
                tick={{ fontSize: 10, fill: spec.color }}
                tickFormatter={(value) => formatValue(String(value), spec.type, 0)}
                tickLine={false}
                axisLine={false}
              />
            ))}
            <Tooltip
              content={({ active, payload }) => {
                const point = payload?.[0]?.payload as Overview["series"][number] | undefined;
                return active && point ? (
                  <div className="m2-chart-tooltip">
                    <b>{point.date}</b>
                    {selectedSpecs.map((spec) => (
                      <p key={spec.key} style={{ color: spec.color }}>
                        {spec.label}：
                        {point.missing
                          ? "缺日"
                          : formatValue(point.metrics[spec.key]?.value, spec.type)}
                        <br />
                        <small>
                          去年同期 {point.previous_date ?? "无对应日"}：
                          {formatValue(point.previous[spec.key]?.value, spec.type)}
                        </small>
                      </p>
                    ))}
                  </div>
                ) : null;
              }}
            />
            {selectedSpecs.flatMap((spec) => [
              <Line
                key={spec.key}
                type="linear"
                yAxisId={spec.key}
                dataKey={`${spec.key}_now`}
                name={spec.label}
                stroke={spec.color}
                dot={false}
                activeDot={{ r: 4 }}
                connectNulls={false}
                strokeWidth={3}
                isAnimationActive={false}
              />,
              ...(series.some((point) => point.previous[spec.key]?.value != null)
                ? [
                    <Line
                      key={`${spec.key}-prior`}
                      type="linear"
                      yAxisId={spec.key}
                      dataKey={`${spec.key}_prior`}
                      name={`${spec.label}去年同期`}
                      stroke={spec.color}
                      strokeDasharray="6 5"
                      strokeWidth={2}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                    />,
                  ]
                : []),
            ])}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!series.length && <p className="m2-empty">当前范围暂无可用趋势记录</p>}
    </>
  );
}
