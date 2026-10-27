import type { DayRow, Product, ProductAlert, Score, ScoreMetric } from "../api/contracts";
import { productKey } from "../state/filters";

/** 全部为有限、可识别的合成UI样例，不含公司快照，不用于业务规则验收。 */
export const PREVIEW_RANGE = { from: "2026-10-01", to: "2026-10-14" };
const definitions = [
  ["demo-official", "示例官方店", "sample-001", "示例轻羽绒绒裤", "裤装", "新品", "示例运营甲"],
  ["demo-outdoor", "示例户外店", "sample-001", "示例轻羽绒绒裤", "裤装", "新品", "示例运营乙"],
  ["demo-official", "示例官方店", "sample-002", "示例暖云抓绒衣", "外套", "新品", "示例运营甲"],
  ["demo-clothing", "示例服饰店", "sample-003", "示例萌趣护耳帽", "帽子", "新品", "示例运营乙"],
  ["demo-outdoor", "示例户外店", "sample-004", "示例小雪盾外套", "外套", "新品", "示例运营乙"],
  ["demo-clothing", "示例服饰店", "sample-005", "示例云朵保温杯", "水杯", "新品", "示例运营甲"],
  ["demo-official", "示例官方店", "sample-006", "示例软软围巾", "围巾", "A级", "示例运营甲"],
  ["demo-outdoor", "示例户外店", "sample-007", "示例花园雨靴", "雨靴", "清仓", "示例运营乙"],
];
export const products: Product[] = definitions.map(
  ([shopId, shopName, id, name, category, level, operator], i) => ({
    shopId,
    shopName,
    id,
    name,
    category,
    level,
    operator,
    styleCode: `DEMO-${String(i + 1).padStart(3, "0")}`,
    key: productKey(shopId, id),
  }),
);
export const days: DayRow[] = products.flatMap((product, p) =>
  Array.from({ length: 14 }, (_, d) => {
    const date = `2026-10-${String(d + 1).padStart(2, "0")}`;
    const visitors = 95 + p * 12 + d * 9;
    const revenue = p === 7 ? "0.00" : ((p + 1) * 19 + d * 7.5).toFixed(2);
    return {
      key: `${product.key}:${date}`,
      product,
      date,
      visitors,
      revenue,
      conversion: revenue === null ? null : revenue === "0.00" ? 0 : (4 + p) / visitors,
      yoy: revenue === null ? null : (d - 3) / 20,
    };
  }),
);
const metrics = [
  ["uv_7", "近7日访客", "number", 15, "1280", [300, 600, 900, 1200]],
  ["gmv_7", "近7日交易金额", "money", 15, "3260.50", [600, 1400, 2400, 3200]],
  ["units_7", "近7日支付件数", "number", 10, "72", [20, 40, 60, 80]],
  ["fav_7", "近7日收藏", "number", 10, "156", [30, 60, 100, 150]],
  ["conversion", "支付转化率", "percent", 10, "0.048", [0.02, 0.03, 0.04, 0.05]],
  ["uv_value", "UV价值", "money", 10, "2.55", [1, 1.5, 2, 2.5]],
  ["roi", "投产比", "ratio", 10, "3.8", [1.5, 2, 3, 4]],
  ["ctr", "点击率", "percent", 5, "0.04", [0.01, 0.02, 0.03, 0.05]],
  ["ad_cvr", "点击成交转化", "percent", 5, "0.06", [0.02, 0.03, 0.05, 0.07]],
  ["growth", "近7日交易倍数", "ratio", 10, "1.62", [1.1, 1.3, 1.5, 1.8]],
] as const;
// 静态展示档位：与上方样例值/阈值对应，不实现生产评分算法。
const demonstratedPoints = [15, 15, 7.5, 10, 7.5, 10, 7.5, 3.75, 3.75, 7.5];
const demonstratedGrades = [
  "突出",
  "突出",
  "优秀",
  "突出",
  "优秀",
  "突出",
  "优秀",
  "优秀",
  "优秀",
  "优秀",
];
const status = ["complete", "complete", "partial", "partial", "unscored", "unscored"] as const;
export const scores: Score[] = products
  .filter((p) => p.level === "新品")
  .map((product, i) => {
    const scoreMetrics: ScoreMetric[] = metrics.map(
      ([key, label, format, weight, value, tiers], index) => {
        const inactive = status[i] === "unscored" || (status[i] === "partial" && index > 5);
        return {
          key,
          label,
          value: inactive ? null : value,
          format,
          weight,
          tiers: [...tiers],
          grade: inactive ? "待验证" : demonstratedGrades[index],
          points: inactive ? null : demonstratedPoints[index],
          reference: "示例品类×季节×生命周期（非真实参照）",
          sampleSize: 24,
          reason: inactive
            ? status[i] === "unscored"
              ? "示例：观察期样本不足，暂未评分"
              : "示例：推广输入待验证"
            : "",
        };
      },
    );
    const verifiedScore = scoreMetrics.reduce(
      (total, metric) => total + Number(metric.points ?? 0),
      0,
    );
    const pendingWeight = scoreMetrics
      .filter((metric) => metric.points === null)
      .reduce((total, metric) => total + metric.weight, 0);
    return {
      id: `demo-score-${i + 1}`,
      product,
      date: PREVIEW_RANGE.to,
      modelVersion: "demo-v1-not-validated",
      stage: i > 3 ? "观察期" : "放量期",
      age: 7 + i * 4,
      season: "示例秋冬",
      status: status[i],
      score: status[i] === "complete" ? verifiedScore : null,
      verifiedScore,
      pendingWeight,
      recommendation: status[i] === "complete" ? "建议小步放量（样例）" : "暂不放量 · 补齐验证",
      reason: "仅用于验证评分卡和四档弹窗，不代表已验收模型或真实投放建议。",
      metrics: scoreMetrics,
    };
  });
export const alerts: ProductAlert[] = [0, 2, 3].map((p, i) => ({
  id: `demo-alert-${i}`,
  product: products[p],
  priority: i === 0 ? "P0" : i === 1 ? "P1" : "P2",
  signal: i === 0 ? "示例：访客显著下滑" : "示例：转化效率下降",
  current: 84 + i * 10,
  baseline: 176 + i * 20,
  change: -0.52 + i * 0.08,
  trend: [180, 172, 155, 148, 122, 98, 84 + i * 10],
  revenue: (1800 + i * 310).toFixed(2),
  impact: (156 + i * 61).toFixed(2),
  reasons: ["示例规则信号", "原因待核查"],
  action: "核对活动与素材变化，检查流量承接及价格。",
  dueAt: null,
  workflowState: null,
}));
