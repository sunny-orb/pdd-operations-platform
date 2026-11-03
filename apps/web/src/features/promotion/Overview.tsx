import { memo } from "react";
import type { Overview } from "./contracts";
import { periodChangeNote, formatMetric, formatValue, productMetricLabel, specs } from "./format";
import type { MetricKey, Mode } from "./state";
import { Trend, TrendLegend } from "./Trend";
import { StoreTrend } from "./StoreTrend";

export const Metrics = memo(function Metrics({
  data,
  selected,
  toggle,
  mode,
}: {
  data: Overview | undefined;
  selected: MetricKey[];
  toggle: (key: MetricKey) => void;
  mode: Mode;
}) {
  const product = mode === "product";
  const note = (key: MetricKey) => {
    if (!product) return selected.includes(key) ? "趋势已选" : "点击查看趋势（最多双指标）";
    if (key === "revenue") return `净交易额 ${formatMetric(data?.net.net_revenue, 0)}`;
    if (key === "roi") return `净投产比 ${formatMetric(data?.net.net_roi)}`;
    if (["spend", "impressions", "clicks"].includes(key))
      return periodChangeNote(data?.metrics[key]);
    if (key === "ctr") return `点击 ${formatMetric(data?.metrics.clicks)}`;
    if (key === "cvr")
      return `成交 ${formatValue(data?.funnel.find((item) => item.key === "deals")?.value)} 笔`;
    return `曝光 ${formatMetric(data?.metrics.impressions)}`;
  };
  return (
    <section className="metrics" id="promotion-metrics" aria-label="推广八项指标">
      {specs.map((spec) => (
        <button
          key={spec.key}
          type="button"
          className={`metric ${selected.includes(spec.key) ? "selected" : ""}`}
          onClick={() => toggle(spec.key)}
          aria-pressed={selected.includes(spec.key)}
        >
          <span>{product ? productMetricLabel(spec.key) : spec.label}</span>
          <strong>
            {formatValue(
              data?.metrics[spec.key]?.value,
              spec.type,
              product && spec.key !== "cpc" ? 0 : 2,
            )}
          </strong>
          <small>{note(spec.key)}</small>
          {product && <em>{selected.includes(spec.key) ? "趋势已选" : "点击查看趋势"}</em>}
        </button>
      ))}
    </section>
  );
});
export const Trends = memo(function Trends({
  data,
  selected,
  product,
}: {
  data: Overview | undefined;
  selected: MetricKey[];
  product: boolean;
}) {
  const chart = (
    <article className="panel chart-panel" id="promotion-trend">
      <div className="panel-title">
        <div>
          {product && <span>核心指标趋势</span>}
          <h2>
            {product
              ? selected
                  .map((key) =>
                    key === "roi" ? "投产比（ROI）" : specs.find((s) => s.key === key)?.label,
                  )
                  .join(" · ")
              : "核心指标趋势"}
          </h2>
        </div>
        {product ? (
          <TrendLegend data={data} selected={selected} />
        ) : (
          <span>实线：本期 虚线：去年同期</span>
        )}
      </div>
      <p className={product ? "chart-note" : undefined}>
        {product
          ? "每项指标按自身数据区间绘制；悬停查看当日数值，去年同期以同色虚线显示。"
          : "每个指标使用独立坐标轴；当天无数据时断线。悬停查看完整日期和数值。"}
      </p>
      {product ? (
        <Trend data={data} selected={selected} />
      ) : (
        <StoreTrend data={data} selected={selected} />
      )}
    </article>
  );
  if (!product) return chart;
  return (
    <section className="grid">
      {chart}
      <article className="panel funnel">
        <div className="panel-title">
          <div>
            <span>推广漏斗</span>
            <h2>从曝光到成交</h2>
          </div>
        </div>
        <div className="funnel-step">
          <b>{formatMetric(data?.metrics.impressions)}</b>
          <span>曝光</span>
        </div>
        <em>
          ↓ {productMetricLabel("ctr")} {formatMetric(data?.metrics.ctr)}
        </em>
        <div className="funnel-step mid">
          <b>{formatMetric(data?.metrics.clicks)}</b>
          <span>点击</span>
        </div>
        <em>
          ↓ {productMetricLabel("cvr")} {formatMetric(data?.metrics.cvr)}
        </em>
        <div className="funnel-step end">
          <b>{formatValue(data?.funnel.find((item) => item.key === "deals")?.value)}</b>
          <span>成交笔数</span>
        </div>
      </article>
    </section>
  );
});
export const TopDates = memo(function TopDates({ data }: { data: Overview | undefined }) {
  return (
    <section className="panel daily-ranking" id="promotion-top-dates">
      <div className="panel-title">
        <div>
          <span>日期复核参考</span>
          <h2>投产比最低的5天</h2>
        </div>
        <small>按当前筛选范围排序，仅比较可计算的日期</small>
      </div>
      <div className="risk-list">
        {data?.top5.map((row) => (
          <div className="risk" key={row.date}>
            <b>{row.date}</b>
            <span>投产比（ROI） {formatValue(row.roi, "ratio")}</span>
            <span>花费 {formatValue(row.spend, "currency", 0)}</span>
            <span>交易额 {formatValue(row.revenue, "currency", 0)}</span>
          </div>
        ))}
        {!data?.top5.length && (
          <p className="m2-empty">
            {!data
              ? "正在读取日期…"
              : data.current_record_count === "0"
                ? "当前范围暂无推广数据"
                : "当前范围没有可计算投产比的日期"}
          </p>
        )}
      </div>
    </section>
  );
});
export const MissingDays = memo(function MissingDays({
  data,
  open,
  onToggle,
}: {
  data: Overview | undefined;
  open?: boolean;
  onToggle?: (open: boolean) => void;
}) {
  const count = data?.coverage.missing_count ?? 0;
  return count > 0 ? (
    <details
      id="promotion-missing-days"
      className="panel"
      open={open}
      onToggle={(event) => onToggle?.(event.currentTarget.open)}
    >
      <summary>查看暂无数据的日期（{count}条）</summary>
      <p>以下店铺和渠道在所列日期暂无推广数据。</p>
      <div className="store-scroll">
        {data?.coverage.missing?.map((row, index) => (
          <p key={index}>
            {String(row.business_date ?? row.date ?? "—")} ·{" "}
            {String(row.shop_name ?? row.shop ?? "—")} ·{" "}
            {String(
              row.channel_label ??
                (row.channel === "product_promotion"
                  ? "商品推广"
                  : row.channel === "star_shop"
                    ? "明星店铺"
                    : row.channel) ??
                "—",
            )}
            ：暂无数据
          </p>
        ))}
      </div>
      {data?.coverage.missing_truncated === true && <p>共 {count} 条，此处展示前200条。</p>}
    </details>
  ) : null;
});
