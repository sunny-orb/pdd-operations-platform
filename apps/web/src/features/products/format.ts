import { formatFixedDecimal } from "../../shared/format/decimal";
export type Value = string | null | undefined;
export type Kind = "number" | "currency" | "percent" | "ratio";
/** 总览沿原型：金额/计数整数，转化率两位；展示舍入不修改API原值。 */
export function format(value: Value, kind: Kind = "number") {
  if (value == null) return "—";
  const result = formatFixedDecimal(
    value,
    kind === "number" || kind === "currency" ? 0 : 2,
    kind === "percent" ? 2 : 0,
    true,
  );
  return result == null
    ? "—"
    : kind === "currency"
      ? `¥${result}`
      : kind === "percent"
        ? `${result}%`
        : kind === "ratio"
          ? `${result}×`
          : result;
}
/** 评分原型保留最多两位小数，率/倍数固定两位；不用二进制浮点格式化。 */
export function formatSignal(value: Value, kind: Kind = "number") {
  if (value == null) return "—";
  let result = formatFixedDecimal(value, 2, kind === "percent" ? 2 : 0, true);
  if (result == null) return "—";
  if (kind === "number" || kind === "currency") result = result.replace(/\.?0+$/, "");
  return kind === "currency"
    ? `¥${result}`
    : kind === "percent"
      ? `${result}%`
      : kind === "ratio"
        ? `${result}×`
        : result;
}
export const percent = (value: Value) =>
  value == null ? "—" : `${formatFixedDecimal(value, 1, 2, true)}%`;
export const change = (value: Value) =>
  value == null ? "无可比基数" : `${value.startsWith("-") ? "" : "+"}${percent(value)}`;
export const localTime = (value: Value) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "—";
