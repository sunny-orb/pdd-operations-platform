import { Empty } from "antd";
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
const colors = ["#2c56a0", "#377c73", "#a9772a", "#b64b3d", "#357a8b", "#665a97", "#8595a9"];
/** 图形坐标近似Number，提示和列表仍显示API十进制原值；不从Top原因补算经营分母。 */
export function Charts({ data }: { data: Overview | undefined }) {
  const trend = (data?.trend ?? []).map((item) => ({
    ...item,
    value: item.qty === null ? null : Number(item.qty),
  }));
  const reasons = (data?.reasons ?? []).map((item) => ({
    ...item,
    value: item.qty === null ? null : Number(item.qty),
  }));
  const hasReasons = reasons.some((item) => item.value !== null && item.value > 0);
  return (
    <section className="m5-chart-row" id="aftersales-charts">
      <article className="m5-panel">
        <div className="m5-panel-heading">
          <h2>历史趋势</h2>
          <span title="近 8 份报告的实退数量，重叠期间不可累加">按筛选结果展示</span>
        </div>
        <div className="m5-chart" role="img" aria-label="历史报告实退数量柱状图">
          {trend.some((item) => item.qty !== null) ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={trend} margin={{ top: 8, right: 8, bottom: 8, left: 2 }}>
                <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} />
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
                <Bar dataKey="value" fill={colors[0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可核历史报告" />
          )}
        </div>
      </article>
      <article className="m5-panel">
        <div className="m5-panel-heading">
          <h2>原因结构</h2>
          <span>当前报表</span>
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
                  >
                    <Label
                      position="center"
                      value={formatReportValue(
                        data?.metrics.find((item) => item.key === "returned_qty")?.value,
                      )}
                      fontSize={20}
                      fill="#14223a"
                    />
                    {reasons.map((item, index) => (
                      <Cell key={item.name} fill={colors[index % colors.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    content={({ active, payload }) => {
                      const item = payload?.[0]?.payload as (typeof reasons)[number] | undefined;
                      return active && item ? (
                        <div className="m5-chart-tooltip">
                          <b>{item.name}</b>
                          <p>
                            {formatReportValue(item.qty)} 件 ·{" "}
                            {formatReportValue(item.share, "percent")}
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
            <ul className="m5-chart-legend" aria-label="原因占比">
              {reasons.map((item, index) => (
                <li key={item.name} title={`${item.name} ${formatReportValue(item.qty)} 件`}>
                  <i style={{ background: colors[index % colors.length] }} />
                  <span>
                    {item.name} {formatReportValue(item.share, "percent")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </article>
    </section>
  );
}
