import { memo, useEffect, useRef, useState } from "react";
import type { Overview } from "./contracts";
import { formatValue, productMetricLabel, specs } from "./format";
import type { MetricKey } from "./state";

type Props = { data: Overview | undefined; selected: MetricKey[] };
type Point = Overview["series"][number];
// 与线上顺序一致；卡片选择顺序不改变左右轴或指标颜色。
const order: MetricKey[] = [
  "revenue",
  "spend",
  "roi",
  "ctr",
  "cvr",
  "cpc",
  "impressions",
  "clicks",
];
const selectedSpecs = (selected: MetricKey[]) =>
  order
    .filter((key) => selected.includes(key))
    .map((key) => specs.find((spec) => spec.key === key)!);

export const TrendLegend = memo(function TrendLegend({ data, selected }: Props) {
  return (
    <div className="legend">
      {selectedSpecs(selected).map((spec) => (
        <span key={spec.key}>
          <i style={{ background: spec.color }} />
          {spec.key === "roi" ? "投产比（ROI）" : spec.label}
        </span>
      ))}
      {data?.series.some((point) => selected.some((key) => point.previous[key]?.value != null)) && (
        <span className="last-legend">
          <i />
          去年同期（虚线）
        </span>
      )}
    </div>
  );
});

/** 沿线上SVG布局，Number仅绘制坐标；原值/汇总比率由接口给出，缺值逐指标断线。 */
export const Trend = memo(function Trend({ data, selected }: Props) {
  const points = data?.series ?? [];
  const selection = selected.join(",");
  const [cursor, setCursor] = useState<{
    data: Overview | undefined;
    selection: string;
    index: number;
  } | null>(null);
  if (cursor && (cursor.data !== data || cursor.selection !== selection)) setCursor(null);
  const index = cursor?.data === data && cursor?.selection === selection ? cursor.index : null;
  const hovered = index == null ? null : points[index];
  const locate = (i: number) => setCursor({ data, selection, index: i });
  const svg = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(920);
  const height = 332,
    left = 72,
    right = 72,
    top = 22,
    bottom = 36;
  const plotWidth = width - left - right,
    plotHeight = height - top - bottom;
  const x = (i: number) =>
    points.length === 1
      ? left + plotWidth / 2
      : left + (i * plotWidth) / Math.max(1, points.length - 1);
  const charts = selectedSpecs(selected).map((spec) => {
    const raw = (p: Point, prior = false) => (prior ? p.previous : p.metrics)[spec.key];
    const read = (p: Point, prior = false) => {
      const value = raw(p, prior)?.value;
      return value != null && Number.isFinite(Number(value)) ? Number(value) : null;
    };
    const values = points
      .flatMap((p) => [read(p), read(p, true)])
      .filter((v): v is number => v != null);
    const min = values.length ? Math.min(...values) : 0;
    const max = values.length ? Math.max(...values) : 0;
    const padding = Math.max(
      (max - min) * 0.16,
      Math.abs(max || 1) * 0.04,
      spec.type === "percent" ? 0.001 : 0.01,
    );
    const lo = min >= 0 ? Math.max(0, min - padding) : min - padding;
    const hi = max + padding;
    const y = (v: number) => top + ((hi - v) * plotHeight) / (hi - lo);
    const path = (prior: boolean) => {
      let connected = false;
      return points
        .map((p, i) => {
          const value = read(p, prior);
          if (value == null) {
            connected = false;
            return "";
          }
          const command = `${connected ? "L" : "M"}${x(i).toFixed(1)},${y(value).toFixed(1)}`;
          connected = true;
          return command;
        })
        .join(" ");
    };
    const format = (value: string | null | undefined) =>
      formatValue(value, spec.type, spec.key === "cpc" ? 2 : 0);
    return { spec, raw, read, values, lo, hi, y, path, format };
  });
  const hasValues = charts.some((c) => c.values.length);

  // 桌面保留线上比例；窄屏按容器宽度排刻度，避免整张图缩成难读的小字。
  useEffect(() => {
    const element = svg.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const measured = entry.contentRect.width;
      if (measured > 0) setWidth(measured < 620 ? Math.max(300, Math.round(measured)) : 920);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [hasValues]);
  const step = Math.max(
    1,
    Math.ceil(points.length / Math.min(8, Math.max(2, Math.floor(plotWidth / 80)))),
  );
  const labels = points
    .map((_, i) => i)
    .filter(
      (i) =>
        i === 0 || i === points.length - 1 || (i % step === 0 && x(points.length - 1) - x(i) >= 48),
    );
  const tooltipRows = hovered
    ? charts.flatMap((chart) =>
        [false, true].flatMap((prior) => {
          if (prior && !points.some((p) => chart.read(p, true) != null)) return [];
          const metric = chart.raw(hovered, prior);
          const label = productMetricLabel(chart.spec.key);
          return [
            {
              text: `${label}${prior ? " · 去年同期" : ""} ${chart.format(metric?.value)}`,
              color: chart.spec.color,
              prior,
            },
          ];
        }),
      )
    : [];
  // 多选指标时分列，避免浮层越过图框；仅展示原值，不在前端重算比率。
  const columns = width < 620 ? 1 : Math.max(1, Math.ceil(tooltipRows.length / 8));
  const rowsPerColumn = Math.ceil(tooltipRows.length / columns);
  const rowHeight = width < 620 && rowsPerColumn > 8 ? 18 : 24;
  const columnWidth = 280;
  const tipWidth = columns * columnWidth;
  const tipHeight = 42 + rowsPerColumn * rowHeight;
  const tipY = Math.max(1, Math.min(top + 10, height - tipHeight - 1));
  const tipX =
    index == null
      ? 0
      : Math.max(
          0,
          Math.min(
            width - tipWidth,
            x(index) > width - tipWidth - 12 ? x(index) - tipWidth - 12 : x(index) + 12,
          ),
        );
  if (!data) return <p className="m2-empty">正在读取趋势…</p>;
  if (!points.length || !hasValues)
    return (
      <p className="m2-empty">
        {!points.length || data.coverage?.observed_days === 0
          ? "当前范围暂无推广数据"
          : "所选指标暂无可绘制数值"}
      </p>
    );
  return (
    <>
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        className="trend-chart m2-product-trend"
        role="img"
        aria-label="商品推广核心指标趋势：实线本期，虚线去年同期"
        onMouseLeave={() => setCursor(null)}
      >
        {[0, 1, 2, 3].map((n) => (
          <line
            key={n}
            x1={left}
            x2={width - right}
            y1={top + (n * plotHeight) / 3}
            y2={top + (n * plotHeight) / 3}
            stroke="#e7edf3"
          />
        ))}
        {charts.slice(0, 2).map(
          (chart, side) =>
            chart.values.length > 0 &&
            [chart.hi, (chart.hi + chart.lo) / 2, chart.lo].map((value, tick) => (
              <text
                key={`${chart.spec.key}-${tick}`}
                x={side ? width - right + 10 : left - 10}
                y={top + (tick * plotHeight) / 2 + 4}
                textAnchor={side ? "start" : "end"}
                className="axis-value"
                fill={chart.spec.color}
              >
                {chart.format(String(value))}
              </text>
            )),
        )}
        {charts.flatMap((chart) =>
          [false, true].map(
            (prior) =>
              points.some((p) => chart.read(p, prior) != null) && (
                <g key={`${chart.spec.key}-${prior}`}>
                  <path
                    data-series={`${chart.spec.key}-${prior ? "prior" : "now"}`}
                    d={chart.path(prior)}
                    fill="none"
                    stroke={chart.spec.color}
                    strokeOpacity={prior ? 0.68 : 1}
                    strokeWidth={prior ? 2.5 : 3}
                    strokeDasharray={prior ? "7 5" : undefined}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  {points.map((p, i) => {
                    const value = chart.read(p, prior);
                    const isolated =
                      (i === 0 || chart.read(points[i - 1], prior) == null) &&
                      (i === points.length - 1 || chart.read(points[i + 1], prior) == null);
                    return value != null && isolated ? (
                      <circle
                        data-isolated-point
                        key={p.date}
                        cx={x(i)}
                        cy={chart.y(value)}
                        r="2.5"
                        fill={prior ? "white" : chart.spec.color}
                        stroke={chart.spec.color}
                      />
                    ) : null;
                  })}
                </g>
              ),
          ),
        )}
        {points.map((p, i) => (
          <rect
            key={p.date}
            role="button"
            aria-label={`查看${p.date}推广数据`}
            tabIndex={0}
            data-date={p.date}
            className="m2-product-trend-hit"
            x={Math.max(left, x(i) - plotWidth / Math.max(1, points.length - 1) / 2)}
            y={top}
            width={
              points.length === 1
                ? plotWidth
                : Math.min(
                    width - right - Math.max(left, x(i) - plotWidth / (points.length - 1) / 2),
                    plotWidth / (points.length - 1),
                  )
            }
            height={plotHeight}
            fill="transparent"
            onMouseEnter={() => locate(i)}
            onFocus={() => locate(i)}
            onBlur={() => setCursor(null)}
            onClick={() => locate(i)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setCursor(null);
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                const next = Math.max(
                  0,
                  Math.min(points.length - 1, i + (event.key === "ArrowRight" ? 1 : -1)),
                );
                event.currentTarget.parentElement
                  ?.querySelector<SVGElement>(`[data-date="${points[next].date}"]`)
                  ?.focus();
                locate(next);
              }
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                locate(i);
              }
            }}
          />
        ))}
        {hovered && index != null && (
          <g pointerEvents="none">
            <line
              data-cursor-line
              x1={x(index)}
              x2={x(index)}
              y1={top}
              y2={top + plotHeight}
              stroke="#9fb7d7"
              strokeDasharray="4 4"
            />
            {charts.flatMap((chart) =>
              [false, true].map((prior) => {
                const value = chart.read(hovered, prior);
                return value == null ? null : (
                  <circle
                    data-active-point
                    key={`${chart.spec.key}-${prior}`}
                    cx={x(index)}
                    cy={chart.y(value)}
                    r={prior ? 3.5 : 4}
                    fill="#fff"
                    stroke={chart.spec.color}
                    strokeWidth={prior ? 2 : 2.5}
                    strokeDasharray={prior ? "3 2" : undefined}
                  />
                );
              }),
            )}
            <g transform={`translate(${tipX},${tipY})`} className="trend-tooltip">
              <rect width={tipWidth} height={tipHeight} rx="8" />
              <text x="12" y="22" className="trend-tooltip-date">
                {hovered.date}
              </text>
              {hovered.previous_date && (
                <text x="118" y="22" className="trend-tooltip-date">
                  去年 {hovered.previous_date}
                </text>
              )}
              {tooltipRows.map((row, i) => (
                <g
                  key={i}
                  transform={`translate(${Math.floor(i / rowsPerColumn) * columnWidth},${48 + (i % rowsPerColumn) * rowHeight})`}
                >
                  <circle
                    cx="15"
                    cy="-5"
                    r="4"
                    fill={row.prior ? "none" : row.color}
                    stroke={row.color}
                    strokeWidth="1.5"
                  />
                  <text
                    x="26"
                    className="trend-tooltip-value"
                    style={{ fill: `color-mix(in srgb, ${row.color} 60%, white)` }}
                  >
                    {row.text}
                  </text>
                </g>
              ))}
            </g>
          </g>
        )}
        {labels.map((i) => (
          <text
            data-date-label
            key={points[i].date}
            x={x(i)}
            y={height - 12}
            textAnchor="middle"
            className="axis-label"
          >
            {points[i].date.slice(5)}
          </text>
        ))}
      </svg>
    </>
  );
});
