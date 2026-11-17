import { formatFixedDecimal } from "../../shared/format/decimal";
import type { Report } from "./contracts";

export const reportTypeLabels: Record<Report["report_type"], string> = {
  weekly: "周报",
  monthly: "月报",
  custom: "自定义",
};
/** 原值只作精确十进制展示；百分点字段已经是百分点，不再次乘100。 */
export function formatValue(value: string | null | undefined, type = "number") {
  const fixed = formatFixedDecimal(
    value,
    type === "number" ? 2 : 2,
    type === "percent" ? 2 : 0,
    type !== "percent" && type !== "points",
  );
  if (fixed === null) return "—";
  const text = type === "number" ? fixed.replace(/\.00$/, "").replace(/(\.\d)0$/, "$1") : fixed;
  return type === "currency"
    ? `¥${text}`
    : type === "percent"
      ? `${text}%`
      : type === "points"
        ? `${text}个百分点`
        : text;
}
export function localTime(value: string | null | undefined) {
  return !value
    ? "尚无记录"
    : Number.isNaN(Date.parse(value))
      ? value
      : new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

/** 摘要卡和图表沿用周报的一位百分比及两位金额精度。 */
export function formatReportValue(value: string | null | undefined, type?: string) {
  if (type === "currency") return formatFixedDecimal(value, 2, 0, true) ?? "—";
  if (type === "percent") {
    const text = formatFixedDecimal(value, 1, 2);
    return text === null ? "—" : `${text}%`;
  }
  return formatValue(value, type);
}

export function formatRateChange(value: string | null | undefined) {
  const points = formatFixedDecimal(value, 1);
  if (points === null) return "暂无可比较上期";
  if (points === "0.0") return "较上期持平";
  return points.startsWith("-") ? `较上期下降 ${points.slice(1)}pct` : `较上期上升 ${points}pct`;
}
