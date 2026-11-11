import { memo } from "react";
import { Card } from "antd";
import type { FiltersResponse, SalesMetrics, SalesTrendMetric, ShopItem } from "../api/contracts";
import { count, fixed, money, percent } from "../format";
import { performanceMetrics } from "../performanceMetrics";
import { RefundDetails } from "./RefundDetails";
import "./sales-kpis.css";

/** 八卡仅格式化API指标；日期、适用渠道和退款解释共用本次筛选结果。 */
export const SalesKpis = memo(function SalesKpis({
  data,
  shops,
  shopId,
  shopName,
  rangeLabel,
  sources,
  selectedMetrics = [],
  onMetricChange,
}: {
  data?: SalesMetrics;
  shops: ShopItem[];
  shopId: string;
  shopName: string;
  rangeLabel: string;
  sources?: FiltersResponse["data"]["sources"];
  selectedMetrics?: readonly SalesTrendMetric[];
  onMetricChange?: (metric: SalesTrendMetric) => void;
}) {
  const promotionLabel =
    data?.promotion_channels
      ?.map((channel) => (channel.code === "shop_channel_star_shop" ? "明星店铺" : "商品推广"))
      .join("＋") || "—";
  const refundSource = { ...data?.refund_match?.source, ...sources?.shop_refunds };
  const failed = ["failed", "abandoned"].includes(refundSource.last_attempt_status ?? "");
  const kpis = [
    ["shop_revenue", money(data?.shop_revenue), `${rangeLabel} · ${shopName}`, "primary"],
    ["shop_visitors", count(data?.shop_visitors), `${rangeLabel} · 每日访客累计`, ""],
    ["buyer_conversion", percent(data?.buyer_conversion), "成交买家数 ÷ 访客数", ""],
    ["aov", data?.aov == null ? "—" : `¥${fixed(data.aov)}`, "销售金额 ÷ 成交买家数", ""],
    ["refund_rate", percent(data?.refund_rate), "同期退款金额 ÷ 同期销售金额", "danger"],
    ["promotion_spend", money(data?.promotion_spend), promotionLabel, ""],
    ["promotion_ctr", percent(data?.promotion_ctr), "推广点击量 ÷ 曝光量", ""],
    ["promotion_roi", fixed(data?.promotion_roi), "推广成交金额 ÷ 推广花费", "success"],
  ] as const;
  return (
    <div className="sales-kpis" data-testid="sales-kpis">
      {kpis.map(([key, value, note, state]) => (
        <Card
          key={key}
          className={`kpi-card ${state}${selectedMetrics.includes(key) ? " is-selected" : ""}`}
        >
          {onMetricChange && (
            <button
              type="button"
              className="sales-kpi-trend-button"
              aria-label={`查看 ${performanceMetrics[key].label} 趋势`}
              aria-pressed={selectedMetrics.includes(key)}
              aria-controls="sales-performance-trend"
              onClick={() => onMetricChange(key)}
            />
          )}
          <div className="sales-kpi-heading">
            <span>{performanceMetrics[key].label}</span>
            {key === "refund_rate" && (
              <RefundDetails
                data={data}
                shops={shops}
                shopId={shopId}
                shopName={shopName}
                rangeLabel={rangeLabel}
                source={sources?.shop_refunds}
              />
            )}
          </div>
          <strong>{value}</strong>
          <small>{note}</small>
          {key === "refund_rate" && failed && (
            <small className="sales-refund-warning">
              退款数据更新未成功，
              {refundSource.last_success_at ? "当前显示上次成功数据。" : "暂无可用数据。"}
            </small>
          )}
        </Card>
      ))}
    </div>
  );
});
