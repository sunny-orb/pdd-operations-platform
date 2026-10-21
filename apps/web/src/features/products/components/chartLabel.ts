import { displayValue } from "./display";
/** 图表刻度与提示沿用API单位，相对走势也遵循0—1比例契约。 */
export function chartLabel(
  value: number,
  format: "money" | "number" | "percent",
  relative = false,
): string {
  return displayValue(value, relative ? "percent" : format);
}
