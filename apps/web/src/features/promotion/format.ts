import { formatFixedDecimal } from "../../shared/format/decimal";
import type { Metric } from "./contracts";
import type { MetricKey } from "./state";
export const specs: { key: MetricKey; label: string; type: string; color: string }[] = [
  { key: "spend", label: "总花费", type: "currency", color: "#e88429" },
  { key: "revenue", label: "交易额", type: "currency", color: "#1768d9" },
  { key: "roi", label: "实际投产比", type: "ratio", color: "#6a55d7" },
  { key: "impressions", label: "曝光量", type: "number", color: "#3478f6" },
  { key: "clicks", label: "点击量", type: "number", color: "#139e94" },
  { key: "ctr", label: "点击率", type: "percent", color: "#16a394" },
  { key: "cvr", label: "点击转化率", type: "percent", color: "#e85d72" },
  { key: "cpc", label: "CPC", type: "currency", color: "#62758e" },
];
/** 金额/比率只做十进制展示舍入；不在浏览器重算经营口径。 */
export function formatValue(value: string | null | undefined, type = "number", moneyDigits = 2) {
  const result = formatFixedDecimal(
    value,
    type === "number" ? 0 : type === "currency" ? moneyDigits : 2,
    type === "percent" ? 2 : 0,
    type !== "ratio" && type !== "percent",
  );
  return result == null
    ? "—"
    : type === "currency"
      ? `¥${result}`
      : type === "percent"
        ? `${result}%`
        : result;
}
export const formatMetric = (metric: Metric | undefined, moneyDigits = 2) =>
  formatValue(metric?.value, metric?.type, moneyDigits);
export const changeValue = (value: string | null | undefined) =>
  value == null
    ? "不可比"
    : `${value.startsWith("-") || value === "0" ? "" : "+"}${formatValue(value, "percent")}`;
export const localTime = (value: unknown) =>
  typeof value !== "string" || !value
    ? "未知"
    : Number.isNaN(Date.parse(value))
      ? value
      : new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

/** 服务端原值保持精确；诊断文案按已知指标单位显示，不解析叙述字符串重算。 */
export function formatClue(clue: {
  label: string;
  current: string | null;
  previous: string | null;
  change: string | null;
  evidence: string;
}) {
  const type =
    clue.label === "CPC" ? "currency" : ["CTR", "点击转化"].includes(clue.label) ? "percent" : null;
  return type
    ? `${formatValue(clue.previous, type)} → ${formatValue(clue.current, type)}（${changeValue(clue.change)}）`
    : clue.evidence;
}
