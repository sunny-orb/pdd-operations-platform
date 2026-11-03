import type { Board } from "./contracts";
import { format } from "./format";

const steps = [
  { label: "商品浏览量", note: "浏览次数" },
  { label: "商品访客数", note: "每日商品访客累计" },
  { label: "新增收藏量", note: "未扣取消收藏" },
  { label: "支付买家数", note: "每日商品买家累计" },
  { label: "支付件数", note: "成交商品件数" },
];

/** 五项沿用同一总览响应；仅解释含义，不推算人群转化或补齐空值。 */
export function ProductFunnel({ data }: { data?: Board }) {
  return (
    <div className="product-funnel">
      <section className="funnel-card" aria-label="商品流量链路">
        <div className="funnel-heading">
          <span>流量链路</span>
          <b>从浏览到成交</b>
          <small>{data ? `${data.scope.start} 至 ${data.scope.end}` : "—"}</small>
        </div>
        <div className="funnel-steps">
          {steps.map(({ label, note }, i) => {
            const item = data?.funnel[i];
            const partial =
              item?.value != null &&
              item.valid_rows != null &&
              item.observed_rows != null &&
              Number(item.valid_rows) < Number(item.observed_rows);
            const empty =
              item && item.value == null
                ? item.observed_rows === "0"
                  ? "暂无经营记录"
                  : "暂无可用数值"
                : null;
            return (
              <div className="funnel-step" key={label}>
                <span>{label}</span>
                <b>{format(item?.value)}</b>
                <small>{empty ?? note}</small>
                {partial && <small className="product-funnel-partial">已知部分</small>}
                {i < steps.length - 1 && <i aria-hidden="true">→</i>}
              </div>
            );
          })}
        </div>
      </section>
      <details className="product-funnel-details">
        <summary>查看统计说明</summary>
        <p>
          按总览筛选分别累计这五项，不代表同一批人依次浏览、收藏并购买。访客和买家跨商品、跨日期可能重复计入；支付件数可以大于买家数。
        </p>
        <p>
          收藏不含加购。没有经营记录不补0；有记录但部分数值为空时，只合计已知部分。来源：数仓商品日报。
        </p>
      </details>
    </div>
  );
}
