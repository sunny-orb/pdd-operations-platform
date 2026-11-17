import { Empty } from "antd";
import { useId, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  Label,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Overview } from "./contracts";
import { formatReportValue } from "./format";
import { Explanation } from "./Explanation";
const colors = ["#2c56a0", "#377c73", "#a9772a", "#b64b3d", "#357a8b", "#665a97", "#8595a9"];

function formatReasonShare(share: string | null) {
  const formatted = formatReportValue(share, "percent");
  return formatted === "0.0%" && Number(share) > 0 ? "<0.1%" : formatted;
}

/** 图形坐标近似Number，提示和列表仍显示API十进制原值；不从Top原因补算经营分母。 */
export function Charts({
  data,
}: {
  data: Pick<Overview, "trend" | "reasons" | "metrics"> | undefined;
}) {
  const [showAllReasons, setShowAllReasons] = useState(false);
  const reasonLegendId = useId();
  const trend = (data?.trend ?? []).map((item) => ({
    ...item,
    value: item.qty === null ? null : Number(item.qty),
  }));
  const reasons = (data?.reasons ?? []).map((item, index) => ({
    ...item,
    value: item.qty === null ? null : Number(item.qty),
    color: colors[index % colors.length],
    displayShare: formatReasonShare(item.share),
  }));
  const hasReasons = reasons.some((item) => item.value !== null && item.value > 0);
  const mainReasons = reasons.filter((item) => Number.parseFloat(item.displayShare) >= 0.3);
  const hiddenReasonCount = reasons.length - mainReasons.length;
  const expanded = showAllReasons && hiddenReasonCount > 0;
  return (
    <section className="m5-chart-row" id="aftersales-charts">
      <article className="m5-panel">
        <div className="m5-panel-heading">
          <h2>历史趋势</h2>
        </div>
        {trend.some((item) => item.qty !== null) ? (
          <div
            className="m5-trend-scroll"
            role="region"
            aria-label="历史报告实退数量柱状图"
            tabIndex={0}
          >
            <div className="m5-chart" style={{ minWidth: 30 + trend.length * 77 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trend} margin={{ top: 8, right: 8, bottom: 8, left: 2 }}>
                  <XAxis
                    dataKey="id"
                    interval={0}
                    height={42}
                    tickLine={false}
                    tick={({ x, y, payload }) => {
                      const item = trend.find((report) => report.id === payload.value);
                      return item ? (
                        <text x={x} y={y} className="m5-trend-date" textAnchor="middle">
                          <tspan x={x} dy="0.8em">
                            {item.period_start}
                          </tspan>
                          <tspan x={x} dy="1.4em">
                            至 {item.period_end}
                          </tspan>
                        </text>
                      ) : (
                        <g />
                      );
                    }}
                  />
                  <YAxis width={20} tick={false} tickLine={false} />
                  <Tooltip
                    content={({ active, payload }) => {
                      const item = payload?.[0]?.payload as (typeof trend)[number] | undefined;
                      return active && item ? (
                        <div className="m5-chart-tooltip">
                          <b>{item.label}</b>
                          <p>
                            {item.period_start} 至 {item.period_end}
                          </p>
                          <span>实退数量 {formatReportValue(item.qty)}</span>
                        </div>
                      ) : null;
                    }}
                  />
                  <Bar dataKey="value" fill={colors[0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <div className="m5-chart">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可核历史报告" />
          </div>
        )}
        <Explanation label="趋势口径">
          <p>默认展示近8份报告的实退数量；选择报告后按所选报告展示。报告期间可能重叠，不可累加。</p>
        </Explanation>
      </article>
      <article className="m5-panel">
        <div className="m5-panel-heading">
          <h2>原因结构</h2>
        </div>
        <div className={hasReasons ? "m5-reason-layout" : undefined}>
          <div className="m5-chart" role="img" aria-label="退货原因件数结构图">
            {hasReasons ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={reasons}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius="48%"
                    outerRadius="92%"
                    isAnimationActive={false}
                  >
                    <Label
                      position="center"
                      value={formatReportValue(
                        data?.metrics.find((item) => item.key === "returned_qty")?.value,
                      )}
                      fontSize={20}
                      fill="#14223a"
                    />
                    {reasons.map((item) => (
                      <Cell key={item.name} fill={item.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    content={({ active, payload }) => {
                      const item = payload?.[0]?.payload as (typeof reasons)[number] | undefined;
                      return active && item ? (
                        <div className="m5-chart-tooltip">
                          <b>{item.name}</b>
                          <p>
                            {formatReportValue(item.qty)} 件 · {item.displayShare}
                          </p>
                        </div>
                      ) : null;
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可核原因结构" />
            )}
          </div>
          {hasReasons && (
            <div className="m5-reason-legend">
              <ul
                id={reasonLegendId}
                className="m5-chart-legend"
                aria-label="原因占比"
                tabIndex={0}
              >
                {(expanded ? reasons : mainReasons).map((item) => (
                  <li key={item.name} title={`${item.name} ${formatReportValue(item.qty)} 件`}>
                    <i aria-hidden="true" style={{ background: item.color }} />
                    <span>{item.name}</span>
                    <span className="m5-reason-share">{item.displayShare}</span>
                  </li>
                ))}
              </ul>
              {hiddenReasonCount > 0 && (
                <button
                  type="button"
                  className="m5-reason-toggle"
                  aria-controls={reasonLegendId}
                  aria-expanded={expanded}
                  onClick={() => setShowAllReasons(!expanded)}
                >
                  {expanded ? "收起原因" : `展开其余 ${hiddenReasonCount} 项`}
                </button>
              )}
            </div>
          )}
        </div>
      </article>
    </section>
  );
}
