import { Empty } from "antd";
import {
  Bar,
  BarChart,
  CartesianGrid,
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
import { formatValue } from "./format";
const colors = ["#1768d9", "#118362", "#d99018", "#8a67d0", "#cc5869", "#4d96a7", "#9baabd"];
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
  return (
    <section className="m5-chart-row" id="aftersales-charts">
      <article className="m5-panel">
        <div className="m5-panel-heading">
          <h2>历史趋势</h2>
          <span>近8份报告的实退数量</span>
        </div>
        <p>按报告期间展示；滚动窗口可能重叠，不能累加为日趋势。</p>
        <div className="m5-chart" role="img" aria-label="历史报告实退数量柱状图">
          {trend.some((item) => item.qty !== null) ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} width={55} />
                <Tooltip
                  content={({ active, payload }) => {
                    const item = payload?.[0]?.payload as (typeof trend)[number] | undefined;
                    return active && item ? (
                      <div className="m5-chart-tooltip">
                        <b>{item.label}</b>
                        <p>
                          {item.period_start} 至 {item.period_end}
                        </p>
                        <span>实退数量 {formatValue(item.qty)}</span>
                      </div>
                    ) : null;
                  }}
                />
                <Bar dataKey="value" fill={colors[0]} radius={[4, 4, 0, 0]} />
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
          <span>当前报告筛选范围</span>
        </div>
        <p>按原因件数显示；中心为当前范围实退数量分母，未知尾部不补零。</p>
        <div className="m5-chart" role="img" aria-label="退货原因件数结构图">
          {reasons.some((item) => item.qty !== null && Number(item.qty) > 0) ? (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={reasons}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="48%"
                  innerRadius="47%"
                  outerRadius="73%"
                >
                  <Label
                    position="center"
                    value={formatValue(
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
                          {formatValue(item.qty)} 件 · {formatValue(item.share, "percent")}
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
        <ul className="m5-chart-legend">
          {reasons.map((item, index) => (
            <li key={item.name}>
              <i style={{ background: colors[index % colors.length] }} />
              {item.name} <b>{formatValue(item.qty)}</b>{" "}
              <span>{formatValue(item.share, "percent")}</span>
            </li>
          ))}
        </ul>
      </article>
    </section>
  );
}
