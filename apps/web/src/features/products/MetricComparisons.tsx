import { formatFixedDecimal } from "../../shared/format/decimal";
import type { Board } from "./contracts";
import { change, format, type Kind } from "./format";

type Comparison = Board["metrics"][string]["yoy_comparison"];

/** 只展示后端配对结果，不用卡片全量重算涨跌；金额保留两位便于核对。 */
export function MetricComparison({
  comparison,
  kind,
  period,
}: {
  comparison: Comparison;
  kind: Kind;
  period: "yoy" | "mom";
}) {
  const rate = comparison?.change;
  const display = (value: string | null | undefined) =>
    value != null && kind === "currency"
      ? `¥${formatFixedDecimal(value, 2, 0, true)}`
      : format(value, kind);
  return (
    <span className="m1-metric-comparison">
      <em className={rate == null ? "m1-comparison-empty" : rate.startsWith("-") ? "down" : "up"}>
        {period === "yoy" ? "同款同比" : "同款环比"}{" "}
        {rate == null ? (comparison?.message ?? "暂无可比记录") : change(rate)}
      </em>
      <small className="m1-comparison-values">
        <span>
          <span>本期</span>
          <span>{display(comparison?.value)}</span>
        </span>
        <span>
          <span>{period === "yoy" ? "去年" : "前期"}</span>
          <span>{display(comparison?.previous)}</span>
        </span>
      </small>
    </span>
  );
}

/** 两期数值常显在卡片内；展开区仅补充配对边界与来源，避免重复整张表。 */
export function MetricComparisons({ periods }: { periods?: Board["comparison_periods"] }) {
  const range = (period: { start: string; end: string } | null) =>
    period ? `${period.start} 至 ${period.end}` : "无对应日期";
  return (
    <div className="m1-metric-comparisons">
      <p>
        上方数值统计筛选后的全部商品；同比、环比只算同一店铺、同一商品在两期对应日期都有记录的部分，所以比较用的“本期”数值可能不同。
      </p>
      <details>
        <summary>比较说明与数据来源</summary>
        {periods && (
          <dl className="m1-comparison-periods">
            <div>
              <dt>本期</dt>
              <dd>{range(periods.current)}</dd>
            </div>
            <div>
              <dt>同款同比 · 去年同期</dt>
              <dd>{range(periods.yoy)}</dd>
            </div>
            <div>
              <dt>同款环比 · 前一期</dt>
              <dd>{range(periods.mom)}</dd>
            </div>
          </dl>
        )}
        <p>以上是比较时间段，不代表每天都有可比记录。2月29日没有去年对应日期时，不参与同比。</p>
        <p>没有对应记录不当作0；去年或前期数值为0时，不算涨跌。</p>
        <p>来源：数仓商品日报。涨跌按原始数值计算，显示时四舍五入。</p>
      </details>
    </div>
  );
}
