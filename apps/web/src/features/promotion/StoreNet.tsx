import { memo } from "react";
import type { Overview } from "./contracts";
import { formatMetric } from "./format";

/** 根据本次筛选的汇总状态说明原因；金额与比率始终使用接口结果，不在页面重算。 */
function netMessage(data: Overview, channel: string, shop: string): string | null {
  if (data.current_record_count === "0") {
    return channel === "star_shop" && shop && shop !== "663599807"
      ? "该店暂无明星店铺推广数据。"
      : "所选期间暂无推广数据。";
  }
  if (data.net.net_revenue?.value == null) {
    if (channel === "star_shop") return "明星店铺暂无净交易额，无法计算净投产。";
    if (data.net.net_revenue?.status === "missing_channel_input") {
      const product = data.channel_summaries.find((item) => item.channel === "product_promotion");
      const suggestion = product?.net.net_revenue?.value != null ? "可切换“商品推广”查看。" : "";
      return `明星店铺暂无净交易额，无法计算合计。${suggestion}`;
    }
    return "部分推广记录没有净交易额，暂无法计算合计。";
  }
  if (data.net.net_roi?.value == null) {
    const spend = data.metrics.spend?.value;
    return spend != null && Number(spend) === 0
      ? "推广花费为0，不计算净投产。"
      : "暂无完整的推广花费，无法计算净投产。";
  }
  return null;
}

export const StoreNet = memo(function StoreNet({
  data,
  channel = "",
  shop = "",
  failed = false,
}: {
  data: Overview | undefined;
  channel?: string;
  shop?: string;
  failed?: boolean;
}) {
  const message = failed
    ? "暂时无法加载，请重试。"
    : data
      ? netMessage(data, channel, shop)
      : "正在读取推广数据…";
  return (
    <section className="callout">
      <strong>
        净交易额：{formatMetric(data?.net.net_revenue)} · 净投产：{formatMetric(data?.net.net_roi)}
      </strong>
      {message && <p>{message}</p>}
    </section>
  );
});
