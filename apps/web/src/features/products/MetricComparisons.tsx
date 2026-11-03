import type { Board } from "./contracts";
import { change } from "./format";

type Comparison = Board["metrics"][string]["yoy_comparison"];

/** 只展示后端期间增幅及空值原因，前端不重算经营结果。 */
export function MetricComparison({
  comparison,
  period,
  rate,
}: {
  comparison: Comparison;
  period: "yoy" | "mom";
  rate: string | null | undefined;
}) {
  const Tag = period === "yoy" ? "em" : "i";
  return (
    <Tag className={rate == null ? "m1-comparison-empty" : rate.startsWith("-") ? "down" : "up"}>
      {period === "yoy" ? "同比" : "环比"}{" "}
      {rate == null ? (comparison?.message ?? "暂无比较结果") : change(rate)}
    </Tag>
  );
}

/** 主卡沿线上简洁布局，实际日期和需要注意的范围放入折叠区。 */
export function MetricComparisons({
  periods,
  metrics,
}: {
  periods?: Board["comparison_periods"];
  metrics?: Board["metrics"];
}) {
  const notes = [
    ...new Set(
      Object.values(metrics ?? {}).flatMap((metric) =>
        metric.observation_note ? [metric.observation_note] : [],
      ),
    ),
  ];
  const range = (period: { start: string; end: string } | null) =>
    period ? `${period.start} 至 ${period.end}` : "无对应日期";
  return (
    <div className="m1-metric-comparisons">
      <details>
        <summary>比较说明与数据来源</summary>
        {periods && (
          <dl className="m1-comparison-periods">
            <div>
              <dt>本期</dt>
              <dd>{range(periods.current)}</dd>
            </div>
            <div>
              <dt>去年同期</dt>
              <dd>{range(periods.yoy)}</dd>
            </div>
            <div>
              <dt>前一期</dt>
              <dd>{range(periods.mom)}</dd>
            </div>
          </dl>
        )}
        <p>
          涨跌直接用卡片上的本期数值计算。去年只统计本期商品在去年对应日期的记录；前一期按相同筛选条件汇总，不要求每天都有同款。
        </p>
        <p>
          没记录不当作0，也不向更早日期补天数；去年或前期为0时不算涨跌。2月29日没有去年同日记录。
        </p>
        {notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
        <p>来源：数仓商品日报。涨跌按原始数值计算，显示时四舍五入。</p>
      </details>
    </div>
  );
}
