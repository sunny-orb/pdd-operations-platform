import { Empty, Table, Tag } from "antd";
import type { Board } from "./contracts";
import type { Scope } from "./scope";
import { change, format } from "./format";
import { MiniTrend, Trend } from "./Chart";

export function Brief({
  data,
  onSelect,
}: {
  data?: Board;
  onSelect?: (patch: Partial<Scope>) => void;
}) {
  const brief = data?.brief;
  const window = brief ? `${brief.start} 至 ${brief.end}` : "等待来源期间";
  return (
    <section className="hero conclusions-hero" id="product-brief">
      <div className="conclusion-main">
        <div className="scope-bar">
          <span>
            当前数据 <b>{data?.observed_rows ?? "—"}</b> 条商品日记录
          </span>
          <span>所选期 {data ? `${data.scope.start} 至 ${data.scope.end}` : "—"}</span>
          <span>
            去年同期{" "}
            {data?.scope.previous_start && data?.scope.previous_end
              ? `${data.scope.previous_start} 至 ${data.scope.previous_end}`
              : "无同月日可比期间"}
          </span>
          <small>粒度：店铺 × 商品 ID × 日期，非 SKU · 按当前资料归属</small>
        </div>
        <div className="conclusion-heading">
          <div>
            <p className="eyebrow">商品经营分析</p>
            <h1>经营核心结论</h1>
            <p>聚焦增长、风险与机会，帮助运营快速定位关键商品。</p>
          </div>
          <span>完整日未知 · 已观察排序</span>
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
                  key={r.category}
                  onClick={() => onSelect?.({ category: r.category, products: [] })}
                >
                  <b>
                    {i + 1}. {r.category}
                  </b>
                  <small>
                    {format(r.revenue, "currency")} · 较前7日 {change(r.growth)}
                  </small>
                </button>
              ))}
              {!brief?.categories.length && <p>暂无可排序的品类数据</p>}
            </div>
          </article>
          <article className="conclusion-item opportunity">
            <span>近7天热卖商品（包含新品）Top 3</span>
            <small>{window}</small>
            <div className="conclusion-list">
              {brief?.products.map((r, i) => (
                <button
                  type="button"
                  className="conclusion-row"
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>
                    {i + 1}. {r.product_name ?? r.product_id}
                  </b>
                  <small>
                    {r.shop_name} · {r.level ?? "等级未匹配"}
                  </small>
                  <small>
                    {format(r.revenue, "currency")} · 较前7日 {change(r.growth)}
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
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>{r.product_name ?? r.product_id}</b>
                  <small>
                    <span className={`priority ${r.alert.priority.toLowerCase()}`}>
                      {r.alert.priority}
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
          <article className="conclusion-item new">
            <span>新品起势 Top 3</span>
            <small>相邻两个7日观察窗 · {window}</small>
            <div className="conclusion-list">
              {brief?.rising.map((r) => (
                <button
                  type="button"
                  className="conclusion-row"
                  key={r.listing_id}
                  onClick={() => onSelect?.({ products: [r.listing_id] })}
                >
                  <b>{r.product_name ?? r.product_id}</b>
                  <small>
                    {format(r.revenue, "currency")} · 支付转化 {format(r.conversion, "percent")}
                  </small>
                  <small>
                    前7日 {format(r.previous_revenue, "currency")} · 环比 {change(r.growth)}
                  </small>
                </button>
              ))}
              {!brief?.rising.length && <p>暂无具备正基数且观察齐全的起势新品</p>}
            </div>
          </article>
        </div>
        <div className="conclusion-action">
          <b>建议动作</b>
          <p>
            AI经营建议待启用。当前可按下方规则风险逐款核对价格、承接及流量入口；本页不自动创建任务或调整预算。
          </p>
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
        <b>{data?.health.title ?? "增长待验证"}</b>
      </div>
      <p>{data?.health.detail ?? "等待相容本期与去年同期数据。"}</p>
      <small>
        判定规则：来源完整性和所选日历、必需字段齐备后，才启用“规模增长、效率承压”。当前仅展示观察数值。
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
    <section className="metrics">
      {definitions.map(([key, label]) => {
        const m = data?.metrics[key];
        return (
          <button
            className={`metric ${active.includes(key) ? "selected" : ""}`}
            key={key}
            type="button"
            aria-pressed={active.includes(key)}
            onClick={() => toggle(key)}
            title={m?.reason ?? "按数仓原值汇总，完整日未知"}
          >
            <span>{label}</span>
            <strong>
              {format(
                m?.value,
                m?.type ??
                  (key === "revenue" || key === "aov"
                    ? "currency"
                    : key === "conversion"
                      ? "percent"
                      : "number"),
              )}
            </strong>
            <small>去年同期 {format(m?.previous, m?.type)}</small>
            <em className={m?.yoy?.startsWith("-") ? "down" : "up"}>同比 {change(m?.yoy)}</em>
            <i className={m?.mom?.startsWith("-") ? "down" : "up"}>环比 {change(m?.mom)}</i>
            {m?.status !== "observed" && (
              <small className="m1-metric-status">{m?.reason ?? "等待数据"}</small>
            )}
          </button>
        );
      })}
    </section>
  );
}
export function Trends({ data, active }: { data?: Board; active: string[] }) {
  const metric = active[0];
  const definition = data?.metrics[metric];
  const comparing = active.length === 1 && (data?.comparison_series?.length ?? 0) > 1;
  const mode = data?.comparison_mode === "products" ? "已选单款对比" : "品类销售 Top 3";
  return (
    <>
      <section className="period-band">
        <div>
          <span>近 7 / 14 / 30 日趋势</span>
          <b>{definition?.label ?? "交易金额"}周期表现</b>
          <small>环比为相邻等长周期对比</small>
        </div>
        {[7, 14, 30].map((days) => {
          const period = data?.windows.find((w) => w.days === days);
          return (
            <div className="period-stat" key={days}>
              <span>近 {days} 日</span>
              <b>{format(period?.metrics[metric]?.value, definition?.type ?? "currency")}</b>
              <small title={period?.metrics[metric]?.reason ?? "相邻等长观察窗"}>
                {change(period?.metrics[metric]?.change)}
              </small>
              {period?.metrics[metric]?.status === "partial" && (
                <small>已观察小计 · 覆盖不全</small>
              )}
            </div>
          );
        })}
      </section>
      <section className="funnel-card">
        <div className="funnel-heading">
          <span>流量链路</span>
          <b>从流量到支付的观察路径</b>
          <small>浏览量、访客、收藏与成交为独立原值；不是同一人群的严格漏斗，加购未接入。</small>
        </div>
        <div className="funnel-steps">
          {["商品浏览量", "商品访客", "新增收藏量", "支付买家", "支付件数"].map((label, i) => {
            const item = data?.funnel[i];
            return (
              <div className="funnel-step" key={label}>
                <span>{label}</span>
                <b>{format(item?.value)}</b>
                <small>{item?.reason ?? "数仓原值 · 完整性未知"}</small>
                {i < 4 && <i>→</i>}
              </div>
            );
          })}
        </div>
      </section>
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
              {comparing ? (
                data?.comparison_series.slice(0, 7).map((line, i) => (
                  <span className="legend-item" key={line.listing_id}>
                    <i
                      style={{
                        background: [
                          "#1768d9",
                          "#d97932",
                          "#1b9878",
                          "#a46bb1",
                          "#d84a4a",
                          "#30a5c0",
                          "#a28942",
                        ][i],
                      }}
                    />
                    {line.label.split(" · ")[0]}
                  </span>
                ))
              ) : (
                <>
                  <i className="l2" />
                  本期{" "}
                  {active.length === 1 && (
                    <>
                      <i className="l4" />
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
        <aside className="panel insights">
          <span>规则诊断 · 非AI生成</span>
          <h2>经营表现与下一步核查</h2>
          <div className="insight">
            <b>{data?.health.title ?? "等待经营输入"}</b>
            <p>{data?.health.detail ?? "数据未接入时，不生成经营结论。"}</p>
          </div>
          <div className="insight">
            <b>当前关注指标</b>
            <p>
              {definition?.label ?? "交易金额"}{" "}
              {format(definition?.value, definition?.type ?? "currency")}；同比{" "}
              {change(definition?.yoy)}，环比 {change(definition?.mom)}。
            </p>
          </div>
          <div className="insight">
            <b>核查顺序</b>
            <p>先复核来源覆盖及资料冲突，再看高优先级风险；建议是排查方向，不是已确认原因。</p>
          </div>
          <small>
            {data?.basis ?? "数据状态待核"}
            。收藏按日新增累计、未扣取消；推广归因窗口和完整日仍待补证。
          </small>
        </aside>
      </section>
    </>
  );
}
export function Risks({ data }: { data?: Board }) {
  type Row = Board["risks"][number];
  const columns = [
    {
      title: "优先级",
      key: "priority",
      width: 80,
      render: (_: unknown, r: Row) => (
        <span className={`priority ${r.alert.priority.toLowerCase()}`}>{r.alert.priority}</span>
      ),
    },
    {
      title: "商品 / 等级",
      key: "product",
      width: 190,
      render: (_: unknown, r: Row) => (
        <>
          <b>{r.product_name ?? r.product_id}</b>
          <small>
            {r.level} · {r.shop_name}
          </small>
        </>
      ),
    },
    {
      title: "异常指标",
      key: "label",
      width: 135,
      render: (_: unknown, r: Row) => <span title={r.alert.basis}>{r.alert.label}</span>,
    },
    {
      title: "当前值",
      key: "now",
      width: 100,
      render: (_: unknown, r: Row) => format(r.alert.current, r.alert.type),
    },
    {
      title: "对比值",
      key: "before",
      width: 100,
      render: (_: unknown, r: Row) => format(r.alert.baseline, r.alert.type),
    },
    {
      title: "变化",
      key: "change",
      width: 100,
      render: (_: unknown, r: Row) => <span className="down-text">{change(r.alert.change)}</span>,
    },
    {
      title: "近7日趋势",
      key: "trend",
      width: 120,
      render: (_: unknown, r: Row) => (
        <span title={r.alert.basis}>
          <MiniTrend values={r.alert.trend} />
        </span>
      ),
    },
    {
      title: "累计交易额",
      key: "revenue",
      width: 130,
      render: (_: unknown, r: Row) => format(r.period_revenue, "currency"),
    },
    {
      title: "预计损失",
      key: "loss",
      width: 125,
      render: (_: unknown, r: Row) => (
        <span className="loss" title="规则估算，非已发生损失">
          {format(r.alert.expected_loss, "currency")}
        </span>
      ),
    },
    {
      title: "原因标签",
      key: "reasons",
      width: 170,
      render: (_: unknown, r: Row) => (
        <div className="reason-tags">
          {r.alert.reasons.map((v) => (
            <Tag key={v}>{v}</Tag>
          ))}
        </div>
      ),
    },
    {
      title: "建议动作",
      key: "action",
      width: 230,
      render: (_: unknown, r: Row) => r.alert.action,
    },
    { title: "截止时间", key: "deadline", width: 110, render: () => "— · 未启用" },
    { title: "状态", key: "status", width: 85, render: () => <Tag>未启用</Tag> },
  ];
  return (
    <section className="risk-card" id="risk">
      <div className="risk-heading">
        <div>
          <span>可执行风险清单</span>
          <b>异常、影响金额与行动闭环</b>
          <small>
            仅纳入最小样本达标且近8日观察齐全商品；转化率为订单÷访客，区别于主卡买家转化；预计损失为规则估算，人工期限/状态尚未启用。
          </small>
        </div>
        <div className="priority-policy">
          <b>P0</b>
          <span>爆品且严重流量下滑</span>
          <b>P1</b>
          <span>有流量无成交、严重订单转化/流量下滑</span>
          <b>P2</b>
          <span>中度订单转化或流量下滑</span>
        </div>
      </div>
      <Table
        className="m1-risk-table"
        rowKey="listing_id"
        size="small"
        columns={columns}
        dataSource={data?.risks ?? []}
        pagination={{
          pageSize: 10,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 款 · 已观察参考`,
        }}
        scroll={{ x: 1780 }}
        locale={{ emptyText: data ? "当前观察范围暂无满足样本量与异常条件的商品" : "等待风险输入" }}
      />
    </section>
  );
}
