import { useMemo, useState } from "react";
import { Button } from "antd";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  performanceTrendSchema,
  type SalesTrendMetric,
  type SalesTrendPoint,
  type Scope,
} from "../api/contracts";
import { detailPath, useSalesDetail } from "../api/details";
import {
  baselineMetricState,
  dailyMetricState,
  dailyMetricValue,
  dailyRelativeChange,
  performanceMetrics,
} from "../performanceMetrics";
import "./sales-performance-trend.css";

const axisFormat = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumSignificantDigits: 5,
});
const changeFormat = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});

type TrendDay = {
  date: string;
  point: SalesTrendPoint;
  changes: Partial<Record<SalesTrendMetric, number | null>>;
};

/** 按可见曲线的极值向外取整到最近刻度，包含 0% 基准。 */
function trendAxis(series: TrendDay[], metrics: SalesTrendMetric[]) {
  let min = 0;
  let max = 0;
  let hasChanges = false;
  for (const { changes } of series) {
    for (const key of metrics) {
      const change = changes[key];
      if (change == null) continue;
      hasChanges = true;
      min = Math.min(min, change);
      max = Math.max(max, change);
    }
  }
  if (min === max) return { hasChanges, ticks: [-0.05, -0.025, 0, 0.025, 0.05] };

  const idealStep = (max - min) / 5;
  const magnitude = 10 ** Math.floor(Math.log10(idealStep));
  const step = magnitude * ([1, 2, 2.5, 5, 10].find((size) => size >= idealStep / magnitude) ?? 10);
  const tickAt = (index: number) => Number((index * step).toPrecision(14));
  const lower = Math.floor(min / step);
  const upper = Math.ceil(max / step);
  const first = tickAt(lower + 1) <= min ? lower + 1 : lower;
  const last = tickAt(upper - 1) >= max ? upper - 1 : upper;
  const ticks = Array.from({ length: last - first + 1 }, (_, index) => tickAt(first + index));
  return { hasChanges, ticks };
}

function displayedChange(value: number | null | undefined) {
  return value == null ? "—" : changeFormat.format(value);
}

function PointTooltip({ day, metrics }: { day: TrendDay; metrics: SalesTrendMetric[] }) {
  return (
    <div className="sales-performance-tooltip" role="status">
      <div className="sales-performance-tooltip-head">
        <time dateTime={day.date}>{day.date}</time>
        <span>当日原值</span>
        <span>较首日</span>
      </div>
      {metrics.map((key) => {
        const point = day.point.metrics[key];
        const state = dailyMetricState(key, point);
        return (
          <div key={key}>
            <span>
              <i style={{ background: performanceMetrics[key].color }} />
              {performanceMetrics[key].label}
            </span>
            <strong>
              {dailyMetricValue(key, point.value)}
              {state && <small>{state}</small>}
            </strong>
            <strong>{displayedChange(day.changes[key])}</strong>
          </div>
        );
      })}
    </div>
  );
}

function TrendChart({ series, metrics }: { series: TrendDay[]; metrics: SalesTrendMetric[] }) {
  const [narrow, setNarrow] = useState(false);
  const [touch, setTouch] = useState(() => window.matchMedia("(hover: none)").matches);
  const [keyboardIndex, setKeyboardIndex] = useState<number | null>(null);
  const keyboardDay = keyboardIndex === null ? undefined : series[keyboardIndex];
  const tickCount = Math.min(narrow ? 4 : 6, series.length);
  const ticks = Array.from(
    { length: tickCount },
    (_, index) =>
      series[Math.round((index * (series.length - 1)) / Math.max(1, tickCount - 1))].date,
  );
  const showDots = series.length <= (narrow ? 14 : 32);
  const { hasChanges, ticks: axisTicks } = useMemo(
    () => trendAxis(series, metrics),
    [series, metrics],
  );
  return (
    <div
      className="sales-performance-chart"
      onPointerDownCapture={(event) => {
        setKeyboardIndex(null);
        setTouch(event.pointerType === "touch");
      }}
      onPointerMoveCapture={(event) => {
        if (keyboardIndex !== null) setKeyboardIndex(null);
        if (touch && event.pointerType === "mouse") setTouch(false);
      }}
      onBlurCapture={() => setKeyboardIndex(null)}
      onKeyDownCapture={(event) => {
        if (event.key === "Escape") setKeyboardIndex(null);
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        event.stopPropagation();
        const step = event.key === "ArrowRight" ? 1 : -1;
        setKeyboardIndex((index) =>
          Math.max(
            0,
            Math.min(series.length - 1, (index ?? (step > 0 ? -1 : series.length)) + step),
          ),
        );
      }}
    >
      <ResponsiveContainer width="100%" height="100%" onResize={(width) => setNarrow(width < 520)}>
        <LineChart
          data={series}
          margin={{ top: 12, right: narrow ? 12 : 24, left: 0, bottom: 6 }}
          accessibilityLayer
          aria-label="经营指标较首日涨跌幅，使用左右方向键查看日期"
        >
          <CartesianGrid vertical={false} stroke="#dce4ee" horizontal={hasChanges} />
          <XAxis
            dataKey="date"
            ticks={ticks}
            interval={0}
            padding={{ left: 12, right: 12 }}
            tickFormatter={(value: string) => value.slice(5)}
            tick={{ fill: "#5d708a", fontSize: 12 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            width={narrow ? 58 : 68}
            domain={[axisTicks[0], axisTicks[axisTicks.length - 1]]}
            ticks={axisTicks}
            niceTicks="none"
            tickFormatter={(value: number) => axisFormat.format(value)}
            tick={hasChanges ? { fill: "#5d708a", fontSize: 12 } : false}
            tickLine={false}
            axisLine={false}
          />
          {hasChanges && <ReferenceLine y={0} stroke="#9aafc8" />}
          <Tooltip
            trigger={touch ? "click" : "hover"}
            active={keyboardDay ? false : undefined}
            filterNull={false}
            isAnimationActive={touch ? false : "auto"}
            animationDuration={120}
            animationEasing="ease-out"
            cursor={{ stroke: "#b5c5d9" }}
            content={({ active, activeIndex }) => {
              const day =
                active && activeIndex !== undefined ? series[Number(activeIndex)] : undefined;
              return day ? <PointTooltip day={day} metrics={metrics} /> : null;
            }}
          />
          {metrics.map((key) => {
            const { color, label } = performanceMetrics[key];
            return (
              <Line
                key={key}
                name={label}
                dataKey={`changes.${key}`}
                type="linear"
                stroke={color}
                strokeWidth={2}
                dot={({ cx, cy, index }) => {
                  if (series[index]?.changes[key] == null || cx == null || cy == null) return null;
                  const isolated =
                    series[index - 1]?.changes[key] == null &&
                    series[index + 1]?.changes[key] == null;
                  return showDots || isolated ? (
                    <circle className="recharts-line-dot" cx={cx} cy={cy} r={2.5} fill={color} />
                  ) : null;
                }}
                activeDot={
                  keyboardDay ? false : { r: 4, fill: color, stroke: "#fff", strokeWidth: 1.5 }
                }
                connectNulls={false}
                isAnimationActive={false}
              />
            );
          })}
          {keyboardDay && (
            <ReferenceLine x={keyboardDay.date} stroke="#a3b8d3" strokeDasharray="4 4" />
          )}
          {keyboardDay &&
            metrics.map((key) => {
              const change = keyboardDay.changes[key];
              return change == null ? null : (
                <ReferenceDot
                  key={key}
                  x={keyboardDay.date}
                  y={change}
                  r={5}
                  fill={performanceMetrics[key].color}
                  stroke="#fff"
                />
              );
            })}
        </LineChart>
      </ResponsiveContainer>
      {!hasChanges && (
        <p className="sales-performance-chart-empty">首日缺值或为 0，仍可按日期查看原值</p>
      )}
      {keyboardDay && (
        <div className="sales-performance-keyboard-tooltip">
          <PointTooltip day={keyboardDay} metrics={metrics} />
        </div>
      )}
    </div>
  );
}

/** 只查询当前经营范围；八个指标共享一次响应，切卡只改绘图和明细。 */
export function SalesPerformanceTrend({
  scope,
  version,
  shopName,
  rangeLabel,
  metrics,
  onConflict,
}: {
  scope: Scope;
  version?: string;
  shopName: string;
  rangeLabel: string;
  metrics: SalesTrendMetric[];
  onConflict: () => void;
}) {
  const path = detailPath("performance/trend", version, {
    date_from: scope.from,
    date_to: scope.to,
    ...(scope.shop ? { shop_id: scope.shop } : {}),
  });
  const { data, error, retry } = useSalesDetail(path, performanceTrendSchema, onConflict);
  const [showDetails, setShowDetails] = useState(false);
  const points = data?.data.points;
  const baseline = points?.find(({ date }) => date === scope.from);
  const series = useMemo<TrendDay[]>(
    () =>
      points?.map((point) => ({
        date: point.date,
        point,
        changes: Object.fromEntries(
          metrics.map((key) => [
            key,
            dailyRelativeChange(point.metrics[key].value, baseline?.metrics[key].value ?? null),
          ]),
        ),
      })) ?? [],
    [points, baseline, metrics],
  );
  const hasData = points?.some(({ metrics: values }) =>
    metrics.some((key) => values[key].value !== null),
  );
  const label = metrics.length > 1 ? "多指标" : performanceMetrics[metrics[0]].label;
  return (
    <section
      id="sales-performance-trend"
      className="sales-performance-trend"
      aria-labelledby="sales-performance-trend-title"
    >
      <header className="sales-performance-trend-header">
        <h3 id="sales-performance-trend-title">经营趋势</h3>
        <p>
          {shopName} · {rangeLabel}
        </p>
      </header>
      {error ? (
        <div className="sales-performance-chart-message" role="status">
          <span>经营趋势读取失败。</span>
          <Button size="small" aria-label="重试读取经营趋势" onClick={retry}>
            重试
          </Button>
        </div>
      ) : !points ? (
        <p className="sales-performance-chart-message" role="status">
          {path ? "正在读取经营趋势…" : "等待经营数据范围"}
        </p>
      ) : hasData ? (
        <>
          <div className="sales-performance-chart-heading">
            <span className="sales-performance-unit">较首日涨跌幅</span>
            <span>{scope.from} 为基准</span>
          </div>
          <ul className="sales-performance-legend" aria-label="已选趋势指标">
            {metrics.map((key) => {
              const state = baselineMetricState(baseline?.metrics[key].value ?? null);
              return (
                <li key={key}>
                  <i style={{ background: performanceMetrics[key].color }} />
                  {performanceMetrics[key].label}
                  {state && <small>{state}</small>}
                </li>
              );
            })}
          </ul>
          <TrendChart key={path} series={series} metrics={metrics} />
        </>
      ) : (
        <p className="sales-performance-chart-message" role="status">
          所选范围暂无{metrics.length === 1 ? label : "已选指标"}日值
        </p>
      )}
      {points && (
        <details
          className="sales-explanation sales-performance-details"
          open={showDetails}
          onToggle={(event) => setShowDetails(event.currentTarget.open)}
        >
          <summary>查看逐日数据</summary>
          {showDetails && (
            <div className="sales-performance-table-scroll">
              <table aria-label={`${label}逐日数据`}>
                <caption>涨跌幅以 {scope.from} 为基准</caption>
                <thead>
                  <tr>
                    <th scope="col">日期</th>
                    {metrics.map((key) => (
                      <th key={key} scope="col">
                        {performanceMetrics[key].label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {series.map(({ date, point: { metrics: values }, changes }) => (
                    <tr key={date}>
                      <th scope="row">{date}</th>
                      {metrics.map((key) => {
                        const point = values[key];
                        const state = dailyMetricState(key, point);
                        return (
                          <td key={key}>
                            {dailyMetricValue(key, point.value)}
                            {changes[key] != null && <small>{displayedChange(changes[key])}</small>}
                            {state && <small>{state}</small>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </details>
      )}
    </section>
  );
}
