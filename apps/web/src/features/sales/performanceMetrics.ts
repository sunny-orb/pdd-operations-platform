import type { SalesTrendMetric, SalesTrendValue } from "./api/contracts";
import { count, fixed, percent } from "./format";
import { decimalParts, formatFixedDecimal } from "../../shared/format/decimal";

export const performanceMetrics = {
  shop_revenue: { label: "销售金额", unit: "元", color: "#2070bf" },
  shop_visitors: { label: "店铺访客", unit: "人", color: "#d58e20" },
  buyer_conversion: { label: "成交转化率", unit: "%", color: "#3c9da3" },
  aov: { label: "客单价", unit: "元", color: "#9266bf" },
  refund_rate: { label: "退款金额占比", unit: "%", color: "#df6c6c" },
  promotion_spend: { label: "推广花费", unit: "元", color: "#92802c" },
  promotion_ctr: { label: "点击率", unit: "%", color: "#6473ad" },
  promotion_roi: { label: "ROI", unit: "倍", color: "#15805d" },
} as const satisfies Record<SalesTrendMetric, { label: string; unit: string; color: string }>;

export function dailyMetricValue(metric: SalesTrendMetric, value: string | null) {
  if (value === null) return "—";
  switch (performanceMetrics[metric].unit) {
    case "元":
      return `¥${formatFixedDecimal(value, 2, 0, true)}`;
    case "人":
      return `${count(value)} 人`;
    case "%":
      return percent(value);
    case "倍":
      return `${fixed(value)} 倍`;
  }
}

/** 用首日原值比较，缺值或零基准返回空；只在输出图表比例时转换为数值。 */
export function dailyRelativeChange(value: string | null, baseline: string | null) {
  const point = decimalParts(value);
  const base = decimalParts(baseline);
  if (!point || !base || base.coefficient === 0n) return null;
  const scale = Math.max(point.scale, base.scale);
  const amount = point.coefficient * 10n ** BigInt(scale - point.scale);
  const denominator = base.coefficient * 10n ** BigInt(scale - base.scale);
  const delta = (point.negative ? -amount : amount) - (base.negative ? -denominator : denominator);
  const precision = 1_000_000_000_000n;
  const magnitude = delta < 0n ? -delta : delta;
  const rounded = (magnitude * precision + denominator / 2n) / denominator;
  const change = Number(delta < 0n ? -rounded : rounded) / Number(precision);
  return Number.isFinite(change) ? change : null;
}

export function baselineMetricState(value: string | null) {
  if (value === null) return "首日无值";
  return decimalParts(value)?.coefficient === 0n ? "首日为 0" : "";
}

/** 提示框与明细使用同一个点的范围和状态。 */
export function dailyMetricState(metric: SalesTrendMetric, point: SalesTrendValue) {
  switch (point.state) {
    case "observed":
      return "";
    case "partial": {
      const unit = metric.startsWith("promotion_") ? "店铺渠道" : "店铺";
      return `当日有值 ${point.observed}/${point.expected} 个${unit}`;
    }
    case "no_records":
      return "暂无记录";
    case "field_null":
      return "当日有字段为空";
    case "unmatched":
      return "暂无可配对的销售与退款金额";
    case "not_connected":
      return "暂无可用数据";
    case "zero_denominator":
      return "当日分母非正";
  }
}
