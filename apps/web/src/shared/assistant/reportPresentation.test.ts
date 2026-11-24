import { expect, it } from "vitest";
import {
  hasTrend,
  metricMatch,
  questionMetric,
  trendPoints,
  uniqueReports,
} from "./reportPresentation";
import type { BusinessReport } from "./contracts";

it.each([
  { values: ["0", "1e-7"], dates: ["2026-11-20", "2026-11-21"], expected: true },
  { values: [null, "0"], dates: ["2026-11-20", "2026-11-21"], expected: false },
  { values: [true, "1"], dates: ["2026-11-20", "2026-11-21"], expected: false },
  { values: ["1e999", "1"], dates: ["2026-11-20", "2026-11-21"], expected: false },
  { values: ["0", "1"], dates: ["2026-11-20", "2026-11-20"], expected: false },
  { values: ["0", "1"], dates: ["2026-11-31", "2026-12-01"], expected: false },
])("曲线需要两个不同有效日期的有限数值：$values/$dates", ({ values, dates, expected }) => {
  expect(hasTrend(values.map((value, index) => ({ value, date: dates[index] })))).toBe(expected);
});

it("跨缺日断线而不补经营值，坐标换算保留原Decimal供精确提示", () => {
  expect(
    trendPoints(
      [
        { date: "2026-11-20", value: "0.125" },
        { date: "2026-11-23", value: "0" },
      ],
      true,
    ),
  ).toEqual([
    { date: "2026-11-20", raw: "0.125", value: 12.5 },
    { date: "2026-11-21", raw: null, value: null },
    { date: "2026-11-23", raw: "0", value: 0 },
  ]);
});

it("仅对象属性顺序不同的报告也是重复依据，不合并不同能力", () => {
  const base: BusinessReport = {
    kind: "dashboard",
    domain: "sales",
    capability: "summary",
    title: "销售",
    source: "发布销售",
    period: null,
    scope: { shop_id: "s", category: "衣" },
    data_version: "v1",
    versions: {},
    facts: [],
    tables: [],
    notes: [],
    series: [],
  };
  expect(
    uniqueReports([
      base,
      { ...base, scope: { category: "衣", shop_id: "s" } },
      { ...base, capability: "trend" },
    ]),
  ).toHaveLength(2);
});

it.each(["看看投产趋势", "退款趋势怎么样"])("缺少明确所问指标不选择无关曲线：%s", (question) => {
  expect(questionMetric([{ key: "revenue", label: "交易额" }], question)).toBeUndefined();
});

it.each([
  {
    question: "净投产趋势",
    gross: { key: "roi", label: "实际投产比" },
    net: { key: "net_roi", label: "净投产比" },
  },
  {
    question: "净交易额趋势",
    gross: { key: "revenue", label: "交易额" },
    net: { key: "net_revenue", label: "净交易额" },
  },
  {
    question: "net_roi趋势",
    gross: { key: "roi", label: "实际投产比" },
    net: { key: "net_roi", label: "净投产比" },
  },
  {
    question: "net_revenue趋势",
    gross: { key: "revenue", label: "交易额" },
    net: { key: "net_revenue", label: "净交易额" },
  },
])("净指标不被原始指标子串抢选：$question", ({ question, gross, net }) => {
  expect(metricMatch(gross, question)).toBe(0);
  expect(questionMetric([gross, net], question)?.key).toBe(net.key);
});
