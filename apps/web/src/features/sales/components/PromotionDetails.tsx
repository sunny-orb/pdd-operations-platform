import { SourceState } from "./SourceState";
import type { SalesMetrics } from "../api/contracts";
import { fixed, money, percent } from "../format";
export function PromotionDetails({ data }: { data?: SalesMetrics }) {
  return (
    <div className="promotion-source">
      {data?.promotion_channels
        ?.filter((x) => ["failed", "abandoned"].includes(x.source.last_attempt_status ?? ""))
        .map((x) => (
          <p key={x.code}>
            <SourceState
              source={x.source}
              label={x.code.endsWith("star_shop") ? "明星店铺" : "商品推广"}
            />
          </p>
        ))}
      <details>
        <summary>查看推广口径与补齐记录</summary>
        {data?.promotion_channels?.map((x) => (
          <p key={x.code}>
            <b>{x.code.endsWith("star_shop") ? "官方明星店铺" : "商品推广概览"}</b> · 最新日{" "}
            {x.source.observed_through ?? "—"} · 更新时间原文{" "}
            {x.source.source_updated_text ?? "未提供"} · 花费 {money(x.spend)} · 交易额{" "}
            {money(x.revenue)} · 点击率 {percent(x.ctr)} · 来源 ROI {fixed(x.roi)} · 缺少{" "}
            {x.missing_shop_days} 个适用店铺日 · 空值{" "}
            {Object.values(x.null_counts).reduce((a, b) => a + b, 0)} 项<br />
            <SourceState
              source={x.source}
              label={x.code.endsWith("star_shop") ? "明星店铺" : "商品推广"}
            />
          </p>
        ))}
        <p>
          花费、曝光和点击按适用渠道累计；两渠道成交归因包含关系及窗口待核，官方店与全店合计 ROI
          暂不发布，单渠道 ROI 为来源参考。
        </p>
        <p>
          历史补齐：旧代码6条飞书记录引用的3张表当前均返回not_found；5条停投零值仅有历史源码声明，未取得本轮可核输入，均暂不生效。不自动回退或把缺失补零。
        </p>
      </details>
    </div>
  );
}
