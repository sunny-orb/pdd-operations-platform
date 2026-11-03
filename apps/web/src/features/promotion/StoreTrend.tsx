import { useState } from "react";
import type { Overview } from "./contracts";
import { formatValue, specs } from "./format";
import type { MetricKey } from "./state";

const COLORS = ["#1768d9", "#e88429"];

/** 店铺图沿线上布局；坐标用Number，详情保留原十进制值；无值断线不补0。 */
export function StoreTrend({
  data,
  selected,
}: {
  data: Overview | undefined;
  selected: MetricKey[];
}) {
  const selection = selected.join(",");
  const [cursor, setCursor] = useState<{
    data: Overview | undefined;
    selection: string;
    date: string;
    pinned: boolean;
  } | null>(null);
  // 筛选结果或指标改变时，旧日期定位失效，不让旧选择带到新图。
  if (cursor && (cursor.data !== data || cursor.selection !== selection)) setCursor(null);
  const active = cursor?.data === data && cursor?.selection === selection ? cursor : null;
  const locate = (date: string) => {
    if (!active?.pinned) setCursor({ data, selection, date, pinned: false });
  };
  const pin = (date: string) => {
    setCursor(
      active?.pinned && active.date === date ? null : { data, selection, date, pinned: true },
    );
  };
  const leave = () => {
    if (!active?.pinned) setCursor(null);
  };
  const points = data?.series ?? [];
  const hovered = points.find((point) => point.date === active?.date);
  const x = (index: number) => 65 + (index * 770) / Math.max(1, points.length - 1);
  const cursorX = hovered ? x(points.indexOf(hovered)) : null;
  const charts = selected.map((key, index) => {
    const spec = specs.find((item) => item.key === key)!;
    const read = (point: Overview["series"][number], prior = false) => {
      const value = (prior ? point.previous : point.metrics)[key]?.value;
      return value == null ? null : Number(value);
    };
    const values = points
      .flatMap((point) => [read(point), read(point, true)])
      .filter((value): value is number => value != null && Number.isFinite(value));
    const minimum = values.length ? Math.min(...values) : 0;
    const maximum = values.length ? Math.max(...values) : 0;
    const pad = Math.max((maximum - minimum) * 0.15, Math.abs(maximum) * 0.03, 0.001);
    const lo = minimum >= 0 ? Math.max(0, minimum - pad) : minimum - pad;
    const hi = maximum === 0 && minimum === 0 ? 1 : maximum + pad;
    const y = (value: number) => 25 + ((hi - value) / (hi - lo)) * 205;
    const path = (prior = false) => {
      let active = false;
      return points
        .map((point, i) => {
          const value = read(point, prior);
          if (value == null) {
            active = false;
            return "";
          }
          const command = `${active ? "L" : "M"}${x(i)},${y(value)}`;
          active = true;
          return command;
        })
        .join(" ");
    };
    return { key, spec, color: COLORS[index], read, values, lo, hi, y, path };
  });
  // 保留首尾，删除距结束标签过近的中间标签，避免原版月底挤字。
  const step = Math.max(1, Math.ceil(points.length / 8));
  const labels = points
    .map((_, i) => i)
    .filter(
      (i) =>
        i === 0 || i === points.length - 1 || (i % step === 0 && x(points.length - 1) - x(i) >= 48),
    );
  const hasPrior = charts.some((chart) => points.some((point) => chart.read(point, true) != null));
  return (
    <div className="m2-store-trend">
      <svg
        viewBox="0 0 900 310"
        role="img"
        aria-label="店铺推广核心指标趋势：实线本期，虚线去年同期"
        onMouseLeave={leave}
        onKeyDown={(event) => {
          if (event.key === "Escape") setCursor(null);
        }}
      >
        {[25, 127, 230].map((y) => (
          <line key={y} x1="65" x2="835" y1={y} y2={y} stroke="#e1e9f3" />
        ))}
        {cursorX != null && (
          <line
            data-cursor-line
            x1={cursorX}
            x2={cursorX}
            y1="25"
            y2="230"
            stroke="#8b9fb8"
            strokeWidth="1"
            strokeDasharray="3 4"
            vectorEffect="non-scaling-stroke"
            pointerEvents="none"
          />
        )}
        {charts.map((chart, i) => (
          <g key={chart.key}>
            {chart.values.length > 0 && (
              <>
                {[chart.hi, (chart.hi + chart.lo) / 2, chart.lo].map((value, tick) => (
                  <text
                    key={tick}
                    x={i ? 845 : 55}
                    y={29 + tick * 102.5}
                    textAnchor={i ? "start" : "end"}
                    fill={chart.color}
                    fontSize="10"
                  >
                    {formatValue(String(value), chart.spec.type)}
                  </text>
                ))}
                {[false, true].map(
                  (prior) =>
                    points.some((point) => chart.read(point, prior) != null) && (
                      <g key={String(prior)}>
                        <path
                          data-series={`${chart.key}-${prior ? "prior" : "now"}`}
                          d={chart.path(prior)}
                          fill="none"
                          stroke={chart.color}
                          strokeWidth={prior ? 2 : 3}
                          strokeDasharray={prior ? "6 5" : undefined}
                        />
                        {points.map((point, n) => {
                          const value = chart.read(point, prior);
                          const isolated =
                            (n === 0 || chart.read(points[n - 1], prior) == null) &&
                            (n === points.length - 1 || chart.read(points[n + 1], prior) == null);
                          return value != null && isolated ? (
                            <circle
                              key={point.date}
                              cx={x(n)}
                              cy={chart.y(value)}
                              r="3"
                              fill={chart.color}
                            />
                          ) : null;
                        })}
                      </g>
                    ),
                )}
              </>
            )}
            <text x={65 + i * 350} y="300" fill={chart.color} fontSize="13">
              ● {chart.spec.label}
            </text>
          </g>
        ))}
        {hovered && cursorX != null && (
          <g pointerEvents="none" aria-hidden="true">
            {charts.flatMap((chart) =>
              [false, true].map((prior) => {
                const value = chart.read(hovered, prior);
                return value == null ? null : (
                  <circle
                    key={`${chart.key}-${prior}`}
                    data-cursor-dot
                    cx={cursorX}
                    cy={chart.y(value)}
                    r={prior ? 3.2 : 3.6}
                    fill={prior ? "#fff" : chart.color}
                    stroke={prior ? chart.color : "#fff"}
                    strokeWidth="1.6"
                  />
                );
              }),
            )}
            <rect x={cursorX - 29} y="247" width="58" height="21" rx="5" fill="#edf3fb" />
            <text
              x={cursorX}
              y="261"
              textAnchor="middle"
              fontSize="10"
              fill="#344b68"
              fontWeight="600"
            >
              {Number(hovered.date.slice(5, 7))}月{Number(hovered.date.slice(8))}日
            </text>
          </g>
        )}
        {points.map((point, i) => (
          <rect
            key={point.date}
            role="button"
            tabIndex={0}
            aria-label={`查看${point.date}推广数据`}
            aria-pressed={active?.pinned === true && active.date === point.date}
            x={Math.max(65, x(i) - 385 / Math.max(1, points.length - 1))}
            y="20"
            width={
              points.length === 1
                ? 770
                : (i === 0 || i === points.length - 1 ? 385 : 770) / (points.length - 1)
            }
            height="220"
            fill="transparent"
            onMouseEnter={() => locate(point.date)}
            onFocus={() => locate(point.date)}
            onBlur={leave}
            onClick={() => pin(point.date)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                pin(point.date);
              }
            }}
          />
        ))}
        {labels
          .filter((i) => cursorX == null || Math.abs(x(i) - cursorX) >= 44)
          .map((i) => (
            <text
              data-date-label
              key={points[i].date}
              x={x(i)}
              y="260"
              fontSize="10"
              textAnchor="middle"
            >
              {points[i].date.slice(5)}
            </text>
          ))}
      </svg>
      <div className="store-tooltip m2-trend-details" aria-live="polite">
        {hovered ? (
          <>
            <div className="m2-trend-date">
              <strong>
                {hovered.date}
                {active?.pinned && <small>已固定</small>}
              </strong>
              <span>去年 {hovered.previous_date ?? "无对应日"}</span>
            </div>
            {charts.map((chart) => (
              <div
                key={chart.key}
                className="m2-trend-reading"
                role="group"
                aria-label={chart.spec.label}
              >
                <strong style={{ color: chart.color }}>{chart.spec.label}</strong>
                <div className="m2-trend-values">
                  <span>
                    本期{" "}
                    <b>
                      {hovered.metrics[chart.key]?.value == null
                        ? "暂无数据"
                        : formatValue(hovered.metrics[chart.key].value, chart.spec.type)}
                    </b>
                  </span>
                  <span>
                    去年{" "}
                    <b>
                      {hovered.previous[chart.key]?.value == null
                        ? "暂无数据"
                        : formatValue(hovered.previous[chart.key].value, chart.spec.type)}
                    </b>
                  </span>
                </div>
              </div>
            ))}
          </>
        ) : (
          <span className="m2-trend-hint">移到图中查看每日数值，点击可固定</span>
        )}
      </div>
      {points.length > 0 && !hasPrior && <p>所选期间暂无去年同期数据</p>}
      {data && !points.length && <p className="m2-empty">当前范围暂无可用趋势记录</p>}
    </div>
  );
}
