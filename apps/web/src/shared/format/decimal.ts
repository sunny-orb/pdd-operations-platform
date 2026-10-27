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
export const formatFixedDecimal = (
  value: DecimalValue,
  digits: number,
  shift = 0,
  grouping = false,
) => {
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
