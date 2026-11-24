import type { AssistantReport, BusinessReport } from "./contracts";

export type EvidenceReport = AssistantReport | BusinessReport;
export type ReportMode = "default" | "chart" | "evidence";
const day = 86400000;
const metricTerms: Record<string, RegExp> = {
  net_roi: /净(?:投产|ROI)|net[_ -]?roi/i,
  net_revenue: /净(?:成交额|成交金额|交易额|交易金额|销售额|GMV)|net[_ -]?revenue/i,
  revenue: /成交额|成交金额|交易额|交易金额|销售额|GMV|\brevenue\b/i,
  refunds: /退款|refund/i,
  visitors: /访客|浏览人数|UV/i,
  buyers: /买家|支付人数/,
  conversion: /转化率|支付转化/,
  spend: /花费|消耗/,
  roi: /投产|ROI/i,
  impressions: /曝光|展现/,
  clicks: /点击(?:量|数)|点击(?!率|成本|转化)/,
  ctr: /点击率/,
  cvr: /转化率|点击转化/,
  cpc: /单次点击|点击成本/,
};
/** 先识别净指标并移除其词段，防止净口径落入原始指标的子串。 */
function metricIntents(text: string) {
  const intents = new Set<string>();
  for (const key of ["net_roi", "net_revenue"]) {
    if (!metricTerms[key].test(text)) continue;
    intents.add(key);
    text = text.replace(new RegExp(metricTerms[key].source, "gi"), "");
  }
  for (const [key, terms] of Object.entries(metricTerms)) {
    if (!key.startsWith("net_") && terms.test(text)) intents.add(key);
  }
  return intents;
}
export function metricMatch(metric: { key: string; label: string }, question: string) {
  const requested = metricIntents(question);
  const available = metricIntents(metric.key + " " + metric.label);
  if (requested.size && ![...requested].some((intent) => available.has(intent))) return 0;
  if (
    (metric.label && question.includes(metric.label)) ||
    (metric.key && question.toLowerCase().includes(metric.key.toLowerCase()))
  )
    return 3;
  if (requested.size) return 2;
  return Object.values(metricTerms).some((term) => term.test(metric.label) && term.test(question))
    ? 1
    : 0;
}
export function questionMetric<T extends { key: string; label: string }>(
  metrics: T[],
  question: string,
) {
  const best = metrics
    .map((metric) => ({ metric, score: metricMatch(metric, question) }))
    .sort((a, b) => b.score - a.score)[0];
  return best && (best.score || !metricIntents(question).size) ? best.metric : undefined;
}
export function chartNumber(value: unknown): number | null {
  if (value == null || typeof value === "boolean" || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function dateTime(date: string) {
  const value = Date.parse(date + "T00:00:00Z");
  return Number.isFinite(value) && new Date(value).toISOString().slice(0, 10) === date
    ? value
    : null;
}
export function hasTrend(points: { date: string; value: unknown }[]) {
  return (
    new Set(
      points
        .filter((p) => dateTime(p.date) !== null && chartNumber(p.value) !== null)
        .map((p) => p.date),
    ).size >= 2
  );
}
/** 缺日只插入断线点，不补造经营值；提示和明细始终保留原始十进制值。 */
export function trendPoints(points: { date: string; value: unknown }[], percent: boolean) {
  const result: { date: string; raw: unknown; value: number | null }[] = [];
  for (const point of points) {
    const previous = result.at(-1);
    const start = previous && dateTime(previous.date);
    const end = dateTime(point.date);
    if (start != null && end != null && end - start > day)
      result.push({
        date: new Date(start + day).toISOString().slice(0, 10),
        raw: null,
        value: null,
      });
    const value = chartNumber(point.value);
    result.push({
      date: point.date,
      raw: point.value,
      value: value === null ? null : value * (percent ? 100 : 1),
    });
  }
  return result;
}
export const domainNames: Record<string, string> = {
  products: "商品数据",
  promotion_store: "店铺推广",
  promotion_product: "商品推广",
  sales: "店铺销售",
  aftersales: "售后分析",
  weather: "天气",
  data_status: "数据更新",
  definitions: "规则定义",
};
function canChart(report: EvidenceReport) {
  if (report.kind === "dashboard")
    return (
      report.domain !== "definitions" &&
      !/definition|capabilit|discover/.test(report.capability) &&
      report.series.some((series) => series.unit !== "text" && hasTrend(series.points))
    );
  return report.metrics.some((metric) =>
    hasTrend(report.series.map((p) => ({ date: p.date, value: p.values[metric.key] }))),
  );
}
/** 问题需有变化或时序意图；附带序列或单独提到某天不等于用户需要图表。 */
export function trendReport(reports: EvidenceReport[], question: string) {
  const explicitDrawing = /曲线|图表|画图|用图/.test(question);
  const trendQuestion = explicitDrawing || /走势|趋势/.test(question);
  if (
    !explicitDrawing &&
    /阈值|门槛|公式|口径|规则|怎么判|如何判|怎么算|如何算|含义|什么意思|能查什么|能做什么/.test(
      question,
    )
  )
    return undefined;
  if (
    !trendQuestion &&
    !/下滑|下降|上涨|回升|增长|波动|变化|变差|变好|环比|同比|相比|对比|比较|比.+(?:周|月|期)|每天|逐日/.test(
      question,
    )
  )
    return undefined;
  const best = reports
    .filter(canChart)
    .map((report) => ({
      report,
      score: (report.kind === "dashboard"
        ? report.series.filter((series) => series.unit !== "text" && hasTrend(series.points))
        : report.metrics.filter((metric) =>
            hasTrend(
              report.series.map((point) => ({ date: point.date, value: point.values[metric.key] })),
            ),
          )
      ).reduce((score, metric) => Math.max(score, metricMatch(metric, question)), 0),
    }))
    .sort((a, b) => b.score - a.score)[0];
  return best && (best.score || !metricIntents(question).size) ? best.report : undefined;
}
export function uniqueReports(reports: EvidenceReport[]) {
  const seen = new Set<string>();
  return reports.filter((report) => {
    const key = JSON.stringify(report, (_key, value) =>
      value && typeof value === "object" && !Array.isArray(value)
        ? Object.fromEntries(
            Object.keys(value)
              .sort()
              .map((key) => [key, value[key]]),
          )
        : value,
    );
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
