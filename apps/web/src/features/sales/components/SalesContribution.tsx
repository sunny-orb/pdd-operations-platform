import { memo } from "react";
import { Progress } from "antd";
import type { FiltersResponse, ShopItem } from "../api/contracts";
import { fixed, money, percent } from "../format";
import { SalesContributionNote } from "./SalesContributionNote";
import "./sales-contribution.css";

/** 仅格式化贡献金额；万元缩写会舍入成零的小额用元展示，真实零保留原格式。 */
function contributionMoney(value: string) {
  const compact = money(value, true);
  const yuan = fixed(value);
  return compact === "¥0.00万" && yuan !== "0.00" ? `¥${yuan}` : compact;
}

/** 接收后端六店排名及份额，不重新汇总或按选店改变分母。 */
export const SalesContribution = memo(function SalesContribution({
  shops,
  selected,
  onSelect,
  error,
  sources,
}: {
  shops: ShopItem[];
  selected: string;
  onSelect: (id: string) => void;
  error: string | null;
  sources?: FiltersResponse["data"]["sources"];
}) {
  return (
    <section className="dashboard-section store-split" id="shops">
      <div className="section-heading">
        <div>
          <p className="eyebrow">STORE CONTRIBUTION</p>
          <h2>店铺销售贡献</h2>
        </div>
        <p>点击店铺可快速切换至单店指标。</p>
      </div>
      <div className="store-layout">
        <article className="sales-rank">
          <div className="list-head">
            <span>店铺</span>
            <span>销售额 / 占比</span>
          </div>
          {shops.map((shop) => (
            <button
              key={shop.shop_id}
              className={selected === shop.shop_id ? "active" : ""}
              aria-pressed={selected === shop.shop_id}
              onClick={() => onSelect(shop.shop_id)}
            >
              <span className={`rank${shop.rank === 1 ? " first" : ""}`}>{shop.rank ?? "—"}</span>
              <b>{shop.display_name}</b>
              <div>
                <Progress
                  size={{ height: 8 }}
                  percent={
                    shop.sales_share == null
                      ? 0
                      : Math.min(100, Math.max(0, Number(shop.sales_share) * 100))
                  }
                  showInfo={false}
                  strokeColor="var(--sales-blue)"
                  railColor="#e8eef7"
                  aria-hidden="true"
                />
                <strong>
                  {shop.shop_revenue === null ? (
                    <small className="contribution-empty">
                      {shop.field_states.shop_revenue === "no_records" ||
                      shop.coverage.observed_days === 0
                        ? "暂无销售记录"
                        : shop.field_states.shop_revenue === "field_null"
                          ? "销售金额为空"
                          : "销售金额暂不可用"}
                    </small>
                  ) : (
                    <>
                      {contributionMoney(shop.shop_revenue)}{" "}
                      <small>{percent(shop.sales_share, 1)}</small>
                    </>
                  )}
                </strong>
              </div>
            </button>
          ))}
          {!shops.length && (
            <p className="pending-rank">{error ? "读取失败，请重试" : "正在读取销售数据…"}</p>
          )}
        </article>
        <SalesContributionNote shops={shops} sources={sources} error={error} />
      </div>
    </section>
  );
});
