import { SourceState } from "./SourceState";
import type { SalesMetrics, FiltersResponse } from "../api/contracts";
import { fixed, money, percent } from "../format";
export function PromotionDetails({
  data,
  sources,
}: {
  data?: SalesMetrics;
  sources?: FiltersResponse["data"]["sources"];
}) {
  const channels = data?.promotion_channels?.map((x) => ({
    ...x,
    source: { ...x.source, ...sources?.[x.code] },
  }));
  return (
    <div className="promotion-source">
      {channels
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
        {channels?.map((x) => (
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
          两渠道独立、无重复成交，按支付日统计。花费、曝光和点击按适用渠道累计；合计 ROI
          在所选支付日的全部适用渠道和必要字段齐备时按合计成交额 /
          合计花费计算，缺覆盖或输入则留空。单渠道保留来源参考；点击后归因窗口天数仍待来源补证。
        </p>
        <p>
          历史补齐：旧代码6条飞书记录引用的3张表当前均返回not_found；5条停投零值仅有历史源码声明，未取得本轮可核输入，均暂不生效。不自动回退或把缺失补零。
        </p>
      </details>
    </div>
  );
}
