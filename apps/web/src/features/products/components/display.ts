export type DecimalValue = string | number | null | undefined;

/** 仅展开十进制表示；指数是位数，不将金额/比率转换成二进制浮点。 */
export function decimalParts(value: DecimalValue) {
  if (value == null) return null;
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(String(value));
  if (!match) return null;
  const exponent = Number(match[4] ?? "0");
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 400) return null;
  const coefficient = BigInt(match[2] + (match[3] ?? ""));
  return {
    negative: match[1] === "-" && coefficient !== 0n,
    coefficient,
    scale: (match[3]?.length ?? 0) - exponent,
  };
}
export const isNegative = (value: DecimalValue) => decimalParts(value)?.negative ?? false;
const fixed = (value: DecimalValue, digits: number, shift = 0, grouping = false) => {
  const parts = decimalParts(value);
  if (!parts) return null;
  const offset = parts.scale - digits - shift;
  const divisor = 10n ** BigInt(Math.max(offset, 0));
  const amount =
    offset > 0
      ? (parts.coefficient + divisor / 2n) / divisor
      : parts.coefficient * 10n ** BigInt(-offset);
  const base = 10n ** BigInt(digits);
  const whole = amount / base;
  return `${parts.negative && amount ? "-" : ""}${grouping ? whole.toLocaleString("zh-CN") : whole}${digits ? `.${String(amount % base).padStart(digits, "0")}` : ""}`;
};

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
