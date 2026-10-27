import { Card, Segmented, Space, Tag } from "antd";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Trend } from "../api/contracts";
import { SectionState, type QueryView } from "../components/SectionState";
import { displayValue } from "../components/display";
import { chartLabel } from "../components/chartLabel";

export type Comparison = "year" | "products" | "relative";
/** 图表由Recharts负责交互与绘制，只映射服务端曲线，不在浏览器计算经营指标。 */
export function TrendSection({
  query,
  label,
  comparison,
  onComparison,
  hasProducts = true,
}: {
  query: QueryView<Trend>;
  label: string;
  comparison: Comparison;
  onComparison: (value: Comparison) => void;
  hasProducts?: boolean;
}) {
  return (
    <Card
      className="trend-card"
      title={`${label}同比趋势`}
      extra={<Tag variant="filled">趋势分析</Tag>}
    >
      <Segmented
        aria-label="趋势对比方式"
        size="small"
        value={comparison}
        onChange={(value) => onComparison(value as Comparison)}
        options={[
          { value: "year", label: "去年同期" },
          { value: "products", label: "商品对比", disabled: !hasProducts },
          { value: "relative", label: "相对走势", disabled: !hasProducts },
        ]}
      />
      {!hasProducts && <p className="muted">先在经营总览选择商品，即可比较商品或相对走势。</p>}
      <SectionState query={query}>
        {(data) => (
          <>
            <div
              className="trend-chart"
              role="img"
              aria-label={`${label}趋势：${data.points.length}个日期，${data.note}`}
            >
              <ResponsiveContainer width="100%" height={290} minWidth={0}>
                <LineChart
                  data={data.points.map((point) => ({
                    ...point,
                    ...Object.fromEntries(
                      data.series.map((series) => [
                        series.key,
                        point[series.key] == null ? null : Number(point[series.key]),
                      ]),
                    ),
                    original: point,
                  }))}
                  margin={{ top: 24, right: 14, left: 3, bottom: 0 }}
                  accessibilityLayer
                >
                  <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="#e7edf4" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v) => String(v).slice(5)}
                    tick={{ fill: "#8190a5", fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={25}
                  />
                  <YAxis
                    tickFormatter={(v) =>
                      data.mode === "relative" || data.format === "percent"
                        ? chartLabel(Number(v), data.format, data.mode === "relative")
                        : Number(v) >= 10000
                          ? `${(Number(v) / 10000).toFixed(1)}万`
                          : String(v)
                    }
                    tick={{ fill: "#8190a5", fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    width={52}
                  />
                  <Tooltip
                    formatter={(_value, _name, entry) =>
                      displayValue(
                        entry.payload?.original?.[String(entry.dataKey)],
                        data.mode === "relative" ? "percent" : data.format,
                      )
                    }
                    contentStyle={{ borderRadius: 10, borderColor: "#e0e7ef", fontSize: 12 }}
                  />
                  <Legend
                    verticalAlign="top"
                    height={30}
                    iconType="circle"
                    iconSize={7}
                    wrapperStyle={{ fontSize: 12 }}
                  />
                  {data.series.map((series, index) => (
                    <Line
                      key={series.key}
                      dataKey={series.key}
                      name={series.label}
                      stroke={series.color}
                      strokeWidth={2.3}
                      dot={false}
                      activeDot={{ r: 4 }}
                      strokeDasharray={comparison === "year" && index === 1 ? "5 4" : undefined}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <Space wrap className="trend-footnote">
              <span className="muted">
                {data.points[0]?.date} 至 {data.points.at(-1)?.date} · {data.points.length}个日期
              </span>
              <span className="muted">{data.note}</span>
            </Space>
          </>
        )}
      </SectionState>
    </Card>
  );
}
