import { formatFixedDecimal } from "../../shared/format/decimal";
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
