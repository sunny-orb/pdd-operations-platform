import { Tooltip } from "antd";
import type { Overview } from "./contracts";
import { changeValue, formatMetric, formatValue, specs } from "./format";
import type { MetricKey, Mode } from "./state";
import { Trend } from "./Trend";

export function Metrics({
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
      return `较前期 ${changeValue(data?.metrics[key]?.change)}`;
    if (key === "ctr") return `点击 ${formatMetric(data?.metrics.clicks)}`;
    if (key === "cvr")
      return `成交 ${formatValue(data?.funnel.find((item) => item.key === "deals")?.value)} 笔`;
    return `曝光 ${formatMetric(data?.metrics.impressions)}`;
  };
  return (
    <section className="metrics" aria-label="推广八项指标">
      {specs.map((spec) => (
        <Tooltip key={spec.key} title={data?.metrics[spec.key]?.reason || spec.label}>
          <button
            type="button"
            className={`metric ${selected.includes(spec.key) ? "selected" : ""}`}
            onClick={() => toggle(spec.key)}
            aria-pressed={selected.includes(spec.key)}
          >
            <span>{spec.label}</span>
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
        </Tooltip>
      ))}
    </section>
  );
}
export function Trends({
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
              ? selected.map((key) => specs.find((s) => s.key === key)?.label).join(" · ")
              : "核心指标趋势"}
          </h2>
        </div>
        {!product && <span>实线：本期 · 虚线：去年同期（有完整数据时）</span>}
      </div>
      <p className="chart-note">每个指标使用独立坐标轴；缺日断线。悬停查看完整日期和数值。</p>
      <Trend data={data} selected={selected} />
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
        <em>↓ CTR {formatMetric(data?.metrics.ctr)}</em>
        <div className="funnel-step mid">
          <b>{formatMetric(data?.metrics.clicks)}</b>
          <span>点击</span>
        </div>
        <em>↓ CVR {formatMetric(data?.metrics.cvr)}</em>
        <div className="funnel-step end">
          <b>{formatValue(data?.funnel.find((item) => item.key === "deals")?.value)}</b>
          <span>成交笔数</span>
        </div>
      </article>
    </section>
  );
}
export function TopDates({ data }: { data: Overview | undefined }) {
  return (
    <section className="panel daily-ranking">
      <div className="panel-title">
        <div>
          <span>日期复核参考</span>
          <h2>低投产日期 Top 5</h2>
        </div>
        <small>仅为所选日期范围内的 ROI 排序，不作为单日预警评级</small>
      </div>
      <div className="risk-list">
        {data?.top5.map((row) => (
          <div key={row.date} className="risk">
            <b>{row.date}</b>
            <span>ROI {formatValue(row.roi, "ratio")}</span>
            <span>花费 {formatValue(row.spend, "currency", 0)}</span>
            <span>交易额 {formatValue(row.revenue, "currency", 0)}</span>
          </div>
        ))}
        {!data?.top5.length && <p className="m2-empty">当前范围暂无可复核日期</p>}
      </div>
    </section>
  );
}
export function StoreNet({ data }: { data: Overview | undefined }) {
  return (
    <section className="callout">
      <strong>
        净交易额：{formatMetric(data?.net.net_revenue)} · 净投产：{formatMetric(data?.net.net_roi)}
      </strong>
      <p>
        {data?.net.net_revenue?.value == null
          ? data?.net.net_revenue?.reason ||
            "暂无完整数据：所选来源未全部提供净交易额；缺失不以零参与净投产计算。"
          : "仅按所选渠道实际提供的净交易额计算；完整性按来源状态单独展示。"}
      </p>
    </section>
  );
}
export function MissingDays({ data }: { data: Overview | undefined }) {
  const count = data?.coverage.missing_count ?? 0;
  return count > 0 ? (
    <details className="panel">
      <summary>查看缺日与历史覆盖提示（{count}条）</summary>
      <p>未确认缺日不补零、不用商品明细冒充全店汇总；早于来源已知覆盖也视为不可用。</p>
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
            ：{String(row.reason ?? "暂无记录")}
          </p>
        ))}
      </div>
      {data?.coverage.missing_truncated === true && (
        <p>缺日共 {count} 条，此处仅展示前200条；数量按全量范围计算。</p>
      )}
    </details>
  ) : null;
}
