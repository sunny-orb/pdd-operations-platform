import {
  decimalParts,
  formatFixedDecimal as fixed,
  type DecimalValue,
} from "../../../shared/format/decimal";
export { decimalParts, isNegative } from "../../../shared/format/decimal";
export type { DecimalValue } from "../../../shared/format/decimal";

/** 只格式化后端原值；缺失显示破折号，真实零正常显示，不计算正式指标。 */
export function displayValue(
  value: DecimalValue,
  format: "money" | "number" | "percent" | "ratio" = "number",
): string {
  const p = decimalParts(value);
  if (!p) return "—";
  if (format === "percent") return `${fixed(value, 2, 2)}%`;
  if (format === "ratio") return `${fixed(value, 2)}×`;
  if (format === "money") {
    const formatted = fixed(value, 2, 0, true)!;
    return formatted.startsWith("-") ? `-¥${formatted.slice(1)}` : `¥${formatted}`;
  }
  const raw =
    p.scale > 0
      ? p.coefficient.toString().padStart(p.scale + 1, "0")
      : (p.coefficient * 10n ** BigInt(-p.scale)).toString();
  const whole = p.scale > 0 ? raw.slice(0, -p.scale) : raw;
  const fraction = p.scale > 0 ? raw.slice(-p.scale).replace(/0+$/, "") : "";
  return `${p.negative ? "-" : ""}${BigInt(whole).toLocaleString("zh-CN")}${fraction ? `.${fraction}` : ""}`;
}
export function displayChange(value: DecimalValue): string {
  const parts = decimalParts(value);
  return !parts
    ? "数据不足"
    : `${!parts.negative && parts.coefficient ? "+" : ""}${fixed(value, 1, 2)}%`;
}
