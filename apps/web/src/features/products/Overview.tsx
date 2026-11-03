import { Empty } from "antd";
import dayjs from "dayjs";
import type { Board } from "./contracts";
import type { Scope } from "./scope";
import { change, format } from "./format";
import { Trend } from "./Chart";
import { TREND_COLORS } from "./chartColors";
import { ProductFunnel } from "./ProductFunnel";
import { ProductDiagnosis } from "./ProductDiagnosis";
import { PeriodWindows } from "./PeriodWindows";
import { MetricComparison, MetricComparisons } from "./MetricComparisons";
import { formatProductLevel } from "../../shared/products/levels";

export function Brief({
  data,
  onSelect,
  updating = false,
}: {
  data?: Board;
  onSelect?: (patch: Partial<Scope>) => void;
  updating?: boolean;
}) {
  const brief = data?.brief;
  const actions = brief?.actions;
  const window = brief ? `${brief.start} 至 ${brief.end}` : "等待来源期间";
  const periodDays = data ? dayjs(data.scope.end).diff(dayjs(data.scope.start), "day") + 1 : null;
  return (
    <section className="hero conclusions-hero" id="product-brief" aria-busy={updating}>
      <div className="conclusion-main">
        <div className="scope-bar">
          <span className="scope-count">
            当前范围 <b>商品-日明细 {format(data?.observed_rows)} 条</b>
          </span>
          <span>
            {data ? `近${periodDays}天（${data.scope.start} 至 ${data.scope.end}）` : "期间待接收"}
          </span>
          <span>
            去年同期{" "}
            {data?.scope.previous_start && data?.scope.previous_end
              ? `${data.scope.previous_start} 至 ${data.scope.previous_end}`
              : "无同月日可比期间"}
          </span>
          <small>粒度：店铺 × 商品 ID × 日期，非 SKU</small>
        </div>
        <div className="conclusion-heading">
          <div>
            <p className="eyebrow">商品数据分析</p>
            <h1>经营核心结论</h1>
          </div>
          <span>聚焦增长、风险与机会</span>
        </div>
        <div className="conclusion-grid">
          <article className="conclusion-item growth">
            <span>近7天热卖品类 Top 3</span>
            <small>{window}</small>
            <div className="conclusion-list">
              {brief?.categories.map((r, i) => (
                <button
                  type="button"
                  className="conclusion-row"
                  disabled={updating}
                  key={r.category}
                  onClick={() => onSelect?.({ category: r.category, products: [] })}
                >
                  <b>
                    {i + 1}. {r.category}
                  </b>
                  <small>
                    交易 {format(r.revenue, "currency")} · 较前7日{" "}
                    {r.growth == null ? (r.comparison_reason ?? "对比数据不足") : change(r.growth)}
                  </small>
                </button>
              ))}
              {!brief?.categories.length && <p>暂无可排序的品类数据</p>}
            </div>
          </article>
          <article className="conclusion-item established-card">
            <span>近7天热卖商品（包含新品）Top 3</span>
            <small>{window}</small>
            <div className="conclusion-list">
              {brief?.products.map((r, i) => (
                <button
                  type="button"
                  className="conclusion-row"
                  disabled={updating}
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>
                    {i + 1}. {r.product_name ?? r.product_id}
                  </b>
                  <small>
                    {r.shop_name} · {formatProductLevel(r.level)}
                  </small>
                  <small>
                    交易 {format(r.revenue, "currency")} · 较前7日 {change(r.growth)}
                  </small>
                </button>
              ))}
              {!brief?.products.length && <p>暂无可排序的商品数据</p>}
            </div>
          </article>
          <article className="conclusion-item risk">
            <span>异常 / 关注款 Top 3</span>
            <small>所选期间累计交易额 · 风险截至 {brief?.end ?? "—"}</small>
            <div className="conclusion-list">
              {brief?.risks.map((r) => (
                <button
                  type="button"
                  className="conclusion-row"
                  disabled={updating}
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>{r.product_name ?? r.product_id}</b>
                  <small>
                    <span className={`priority ${r.alert.priority.toLowerCase()}`}>
                      {r.alert.priority === "P0" ? "P0紧急" : r.alert.priority}
                    </span>{" "}
                    {r.alert.label} · 累计交易 {format(r.period_revenue, "currency")}
                  </small>
                </button>
              ))}
              {!brief?.risks.length && (
                <p>{data ? "当前观察范围暂无满足条件的异常" : "等待风险输入"}</p>
              )}
            </div>
          </article>
          <article className="conclusion-item opportunity">
            <span>新品起势 Top 3</span>
            <small>相邻两个7日观察窗 · {window}</small>
            <div className="conclusion-list">
              {brief?.rising.map((r) => (
                <button
                  type="button"
                  className="conclusion-row"
                  disabled={updating}
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>{r.product_name ?? r.product_id}</b>
                  <small>
                    交易 {format(r.revenue, "currency")} · 支付转化{" "}
                    {format(r.conversion, "percent")}
                  </small>
                  <small>
                    前7日交易 {format(r.previous_revenue, "currency")} · 环比 {change(r.growth)}
                  </small>
                </button>
              ))}
              {!brief?.rising.length && <p>暂无具备正基数且观察齐全的起势新品</p>}
            </div>
          </article>
        </div>
        <div className="conclusion-action" role="region" aria-label="建议动作">
          <b>
            建议动作<small>规则提醒</small>
          </b>
          <div className="conclusion-action-content">
            {!!actions?.items.length && (
              <ul className="conclusion-action-summary">
                {actions.items.map((item) => (
                  <li key={item.kind}>{item.text}</li>
                ))}
              </ul>
            )}
            {!actions?.items.length && (
              <p className="conclusion-action-status">
                {actions?.message ?? "等待当前范围的规则提醒。"}
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
export function Health({ data }: { data?: Board }) {
  return (
    <section className={`health-bar ${data?.health.tone ?? "neutral"}`}>
      <div>
        <span>增长健康度</span>
        <b>{data?.health.title ?? "暂无比较结果"}</b>
      </div>
      <p>{data?.health.detail ?? "取得所选范围的商品记录后，展示同款商品的同期变化。"}</p>
      <small>
        同店同商品按去年同月同日配对，无对应记录不参与。交易额同比增长且转化率或客单价下降，标记为“规模增长、效率承压”。
      </small>
    </section>
  );
}
export function Metrics({
  data,
  active,
  toggle,
}: {
  data?: Board;
  active: string[];
  toggle: (key: string) => void;
}) {
  const definitions = [
    ["revenue", "交易金额"],
    ["visitors", "商品访客数"],
    ["favorites", "新增收藏量"],
    ["buyers", "支付买家数"],
    ["units", "支付件数"],
    ["aov", "客单价"],
    ["conversion", "支付转化率"],
  ];
  return (
    <>
      <section className="metrics">
        {definitions.map(([key, label]) => {
          const m = data?.metrics[key];
          const kind =
            m?.type ??
            (key === "revenue" || key === "aov"
              ? "currency"
              : key === "conversion"
                ? "percent"
                : "number");
          return (
            <button
              className={`metric ${active.includes(key) ? "selected" : ""}`}
              key={key}
              type="button"
              aria-pressed={active.includes(key)}
              onClick={() => toggle(key)}
            >
              <span className="m1-metric-label">{label}</span>
              <strong>{format(m?.value, kind)}</strong>
              <small>去年同期 {format(m?.previous, kind)}</small>
              <MetricComparison comparison={m?.yoy_comparison} rate={m?.yoy} period="yoy" />
              <MetricComparison comparison={m?.mom_comparison} rate={m?.mom} period="mom" />
              {m?.value == null && (
                <small className="m1-metric-status">{!m ? "等待数据" : "暂无可用数值"}</small>
              )}
            </button>
          );
        })}
      </section>
      <MetricComparisons periods={data?.comparison_periods} metrics={data?.metrics} />
    </>
  );
}
export function Trends({ data, active }: { data?: Board; active: string[] }) {
  const metric = active[0];
  const definition = data?.metrics[metric];
  const comparing = active.length === 1 && (data?.comparison_series?.length ?? 0) > 1;
  const mode = data?.comparison_mode === "products" ? "已选单款对比" : "品类销售 Top 3";
  return (
    <>
      <PeriodWindows data={data} metric={metric} />
      <ProductFunnel data={data} />
      <section className="dashboard-grid" id="product-diagnosis">
        <article className="panel chart-panel">
          <div className="panel-title">
            <div>
              <span>{active.length > 1 ? "多指标趋势" : comparing ? mode : "同比趋势"}</span>
              <h2>
                {active.length > 1
                  ? "已选指标趋势"
                  : `${definition?.label ?? "交易金额"}${comparing ? mode : "同比"}趋势`}
              </h2>
            </div>
            <div className="legend">
              {active.length > 1 ? (
                active.map((key, i) => (
                  <span className="legend-item" key={key}>
                    <i style={{ background: TREND_COLORS[i % TREND_COLORS.length] }} />
                    {data?.metrics[key]?.label}
                  </span>
                ))
              ) : comparing ? (
                data?.comparison_series.slice(0, 7).map((line, i) => (
                  <span className="legend-item" key={line.listing_id}>
                    <i
                      style={{
                        background: TREND_COLORS[i % TREND_COLORS.length],
                      }}
                    />
                    {line.label.split(" · ")[0]}
                  </span>
                ))
              ) : (
                <>
                  <i style={{ background: TREND_COLORS[0] }} />
                  本期{" "}
                  {active.length === 1 && (
                    <>
                      <i style={{ background: "#9aa8ba" }} />
                      去年同期
                    </>
                  )}
                </>
              )}
            </div>
          </div>
          {active.length > 1 && (
            <p className="chart-note">指标按各自刻度绘制，悬浮查看实际数值。</p>
          )}
          {data ? (
            <Trend data={data} metrics={active} />
          ) : (
            <div className="m1-chart-empty">
              <Empty description="暂无已发布趋势" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          )}
        </article>
        <ProductDiagnosis data={data} metric={metric} />
      </section>
    </>
  );
}
