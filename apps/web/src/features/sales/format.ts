import { formatFixedDecimal } from "../../shared/format/decimal";

export const count = (value: string | null | undefined) =>
  value == null ? "—" : formatFixedDecimal(value, 0, 0, true)!;
export const fixed = (value: string | null | undefined, digits = 2) =>
  value == null ? "—" : formatFixedDecimal(value, digits)!;
export const percent = (value: string | null | undefined, digits = 2) =>
  value == null ? "—" : `${formatFixedDecimal(value, digits, 2)}%`;
export function money(value: string | null | undefined, compact = false) {
  if (value == null) return "—";
  if (compact) {
    const digits = BigInt(value.split(".")[0]) >= 100000n ? 1 : 2;
    return `¥${formatFixedDecimal(value, digits, -4)}万`;
  }
  return `¥${formatFixedDecimal(value, 0, 0, true)}`;
}
export const localTime = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })
    : "暂无成功记录";
