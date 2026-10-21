/** 只做展示格式化，不计算经营指标；金额通过十进制字符串舍入，保留null语义。 */
export function displayValue(
  value: string | number | null | undefined,
  format: "money" | "number" | "percent" | "ratio" = "number",
): string {
  if (value == null || !/^-?\d+(\.\d+)?$/.test(String(value))) return "—";
  const raw = String(value);
  if (format === "percent") return `${(Number(raw) * 100).toFixed(2)}%`;
  if (format === "ratio") return `${Number(raw).toFixed(2)}×`;
  if (format === "money") {
    const negative = raw.startsWith("-");
    const [whole, fraction = ""] = raw.replace(/^-/, "").split(".");
    const cents =
      BigInt(whole) * 100n +
      BigInt(fraction.padEnd(3, "0").slice(0, 2)) +
      (Number(fraction[2] ?? 0) >= 5 ? 1n : 0n);
    return `${negative && cents ? "-" : ""}¥${(cents / 100n).toLocaleString("zh-CN")}.${String(cents % 100n).padStart(2, "0")}`;
  }
  const [whole, fraction] = raw.split(".");
  const meaningfulFraction = fraction?.replace(/0+$/, "");
  return `${BigInt(whole).toLocaleString("zh-CN")}${meaningfulFraction ? `.${meaningfulFraction}` : ""}`;
}
export function displayChange(value: number | null): string {
  return value == null ? "数据不足" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}
