import type {
  Brief,
  DayRow,
  Diagnosis,
  Envelope,
  Filters,
  Meta,
  Metric,
  MetricKey,
  Options,
  Overview,
  Product,
  RankItem,
  RequestOptions,
  Scope,
  Scores,
  Trend,
} from "../api/contracts";
import { alerts, days, PREVIEW_RANGE, products, scores } from "./fixtures";

const VERSION = "development-synthetic-v1";
const meta = (partial: boolean): Meta => ({
  dataVersion: VERSION,
  dataThrough: PREVIEW_RANGE.to,
  lastSuccessAt: "2026-10-14T18:00:00+08:00",
  completeness: partial ? "partial" : "complete",
  missingReasons: partial ? ["开发样例：部分金额或去年同期输入缺失，不可计算项保留为空。"] : [],
  isPreview: true,
});
const matchProduct = (p: Product, f: Filters) =>
  (!f.shop || p.shopId === f.shop) &&
  (!f.operator || p.operator === f.operator) &&
  (!f.category || p.category === f.category) &&
  (!f.level || p.level === f.level) &&
  (!f.products.length || f.products.includes(p.key)) &&
  (!f.keyword || `${p.name} ${p.id}`.toLowerCase().includes(f.keyword.toLowerCase()));
const matchDay = (row: DayRow, f: Filters) =>
  matchProduct(row.product, f) &&
  (!f.dateFrom || row.date >= f.dateFrom) &&
  (!(f.dateTo || f.dateFrom) || row.date <= (f.dateTo || f.dateFrom));
const sumMoney = (rows: DayRow[]) => rows.reduce((sum, row) => sum + Number(row.revenue ?? 0), 0);
const moneyValue = (rows: DayRow[]) =>
  rows.some((row) => row.revenue === null) ? null : sumMoney(rows).toFixed(2);
const sumVisitors = (rows: DayRow[]) =>
  rows.reduce((sum, row) => sum + Number(row.visitors ?? 0), 0);
const unique = (values: string[]) => [...new Set(values)];
const paginate = <T>(items: T[], filters: Filters) =>
  items.slice((filters.page - 1) * filters.pageSize, filters.page * filters.pageSize);
const rank = (p: Product, rows: DayRow[]): RankItem => ({
  product: p,
  name: p.name,
  value: moneyValue(rows.filter((r) => r.product.key === p.key)),
  change: 0.18,
  note: `${p.shopName} · ${p.level}`,
});
const dayOffset = (day: string, n: number) =>
  new Date(Date.parse(`${day}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);

/** 小型开发适配模拟后端同形响应；只处理合成数据，不作为生产计算或评分实现。 */
export async function getPreviewResponse(
  endpoint: string,
  _scope: Scope,
  filters: Filters,
  options: RequestOptions,
): Promise<Envelope<unknown>> {
  if (options.scenario === "error" && endpoint !== "filters")
    throw new Error("开发样例：模拟接口请求失败，请重试或切换场景。");
  if (options.dataVersion && options.dataVersion !== VERSION)
    throw Object.assign(new Error("数据版本已变化，请重新加载当前范围。"), { status: 409 });
  const sourceDays: DayRow[] =
    options.scenario === "partial"
      ? days.map((row) =>
          row.product.id === "sample-005" && Number(row.date.slice(-2)) % 4 === 1
            ? { ...row, revenue: null, conversion: null, yoy: null }
            : row,
        )
      : days;
  const missingAmount =
    !endpoint.startsWith("scores") &&
    endpoint !== "filters" &&
    sourceDays.some((row) => matchDay(row, filters) && row.revenue === null);
  const wrap = (data: unknown, empty = false): Envelope<unknown> => ({
    data: structuredClone(data),
    availability: empty ? "empty" : "ready",
    computationState: "ready",
    meta: meta(options.scenario === "partial" || missingAmount),
  });
  if (options.scenario === "not-connected")
    return {
      data: null,
      availability: "not_connected",
      computationState: "ready",
      meta: { ...meta(false), dataThrough: null, lastSuccessAt: null, completeness: "unknown" },
    };
  if (options.scenario === "empty" && endpoint !== "filters") return wrap(null, true);
  if (options.scenario === "pending" && (endpoint.startsWith("scores") || endpoint === "diagnosis"))
    return { ...wrap(null), computationState: "pending" };
  const activeProducts = products.filter((p) => matchProduct(p, filters));
  const rows = sourceDays.filter((row) => matchDay(row, filters));
  const windowRows = sourceDays.filter(
    (row) =>
      matchProduct(row.product, filters) &&
      row.date <= (filters.dateTo || filters.dateFrom || PREVIEW_RANGE.to),
  );
  const incomplete = options.scenario === "partial" || missingAmount;
  if (endpoint === "filters") {
    const shopProducts = products.filter((p) => !filters.shop || p.shopId === filters.shop);
    const operatorProducts = shopProducts.filter(
      (p) => !filters.operator || p.operator === filters.operator,
    );
    const categoryProducts = operatorProducts.filter(
      (p) => !filters.category || p.category === filters.category,
    );
    const result: Options = {
      shops: [
        ...new Map(
          products.map((p) => [p.shopId, { value: p.shopId, label: p.shopName }]),
        ).values(),
      ],
      operators: unique(shopProducts.map((p) => p.operator)),
      categories: unique(operatorProducts.map((p) => p.category)),
      levels: unique(categoryProducts.map((p) => p.level)),
      products: categoryProducts.filter((p) => !filters.level || p.level === filters.level),
      dateRange: PREVIEW_RANGE,
    };
    return wrap(result);
  }
  if (endpoint === "days") {
    const sorted = [...rows].sort(
      (a, b) => b.date.localeCompare(a.date) || a.product.key.localeCompare(b.product.key),
    );
    return rows.length
      ? wrap({ items: paginate(sorted, filters), total: sorted.length })
      : wrap(null, true);
  }
  if (endpoint === "scores") {
    const selected = scores.filter((s) => matchProduct(s.product, filters));
    const matching = selected.filter((s) => !filters.judgment || s.status === filters.judgment);
    const result: Scores = {
      items: paginate(matching, filters),
      total: matching.length,
      counts: {
        complete: selected.filter((s) => s.status === "complete").length,
        partial: selected.filter((s) => s.status === "partial").length,
        unscored: selected.filter((s) => s.status === "unscored").length,
      },
    };
    return matching.length ? wrap(result) : wrap(null, true);
  }
  if (endpoint.startsWith("scores/")) {
    const selected = scores.find((s) => s.id === endpoint.slice(7));
    return selected ? wrap(selected) : wrap(null, true);
  }
  if (!rows.length) return wrap(null, true);
  const revenue = sumMoney(rows),
    visitors = sumVisitors(rows);
  const buyers = Math.round(visitors * 0.043),
    units = Math.round(buyers * 1.12),
    favorites = Math.round(visitors * 0.08);
  const current: Record<MetricKey, number | null> = {
    revenue: missingAmount ? null : revenue,
    visitors,
    buyers,
    units,
    favorites,
    aov: missingAmount || !buyers ? null : revenue / buyers,
    conversion: visitors ? buyers / visitors : null,
  };
  if (endpoint === "overview") {
    const names: [MetricKey, string, Metric["format"]][] = [
      ["revenue", "交易金额", "money"],
      ["visitors", "商品访客数", "number"],
      ["favorites", "收藏用户数", "number"],
      ["buyers", "支付买家数", "number"],
      ["units", "支付件数", "number"],
      ["aov", "客单价", "money"],
      ["conversion", "支付转化率", "percent"],
    ];
    const result: Overview = {
      metrics: names.map(([key, label, format]) => ({
        key,
        label,
        format,
        value: current[key] === null ? null : current[key].toFixed(format === "percent" ? 4 : 2),
        previous: current[key] === null ? null : (current[key] * 0.92).toFixed(2),
        yearAgo:
          incomplete || current[key] === null
            ? null
            : (current[key] * 0.8).toFixed(format === "percent" ? 4 : 2),
        mom: current[key] === null ? null : 0.087,
        yoy: incomplete ? null : 0.25,
      })),
      periods: [7, 14, 30].map((n) => ({
        days: n,
        value:
          n === 30
            ? null
            : moneyValue(
                windowRows.filter(
                  (r) =>
                    r.date >=
                    dayOffset(filters.dateTo || filters.dateFrom || PREVIEW_RANGE.to, -n + 1),
                ),
              ),
        change: n === 30 || incomplete ? null : 0.18,
      })),
      funnel: [
        { key: "views", label: "浏览量", value: Math.round(visitors * 1.4), note: "商品浏览PV" },
        { key: "visitors", label: "访客", value: visitors, note: "商品访客UV" },
        { key: "favorites", label: "收藏用户", value: favorites, note: "不含加购" },
        { key: "buyers", label: "支付买家", value: buyers, note: "累计买家" },
        { key: "units", label: "支付件数", value: units, note: "成交件数" },
      ],
      health: {
        title: incomplete ? "对比数据待补齐" : "规模增长，关注转化承接",
        description: incomplete
          ? "示例：去年同期不完整，暂不判断增长健康度。"
          : "开发样例规则结论：关注流量与成交效率，所有数字均为合成数据。",
        tone: incomplete ? "info" : "warning",
      },
    };
    return wrap(result);
  }
  if (endpoint === "brief") {
    const end = filters.dateTo || PREVIEW_RANGE.to,
      from = dayOffset(end, -6);
    const weekRows = windowRows.filter((r) => r.date >= from && r.date <= end);
    const categories = unique(weekRows.map((r) => r.product.category))
      .map((name) => ({
        name,
        value: moneyValue(weekRows.filter((r) => r.product.category === name)),
        change: incomplete ? null : 0.18,
        note: "开发样例 · 较前7日",
      }))
      .sort((a, b) => Number(b.value) - Number(a.value))
      .slice(0, 3);
    const ranked = activeProducts
      .map((p) => rank(p, weekRows))
      .sort((a, b) => Number(b.value) - Number(a.value))
      .filter((r) => Number(r.value) > 0);
    const result: Brief = {
      categories,
      products: ranked.slice(0, 3),
      declining: alerts
        .filter((a) => matchProduct(a.product, filters))
        .map((a) => ({
          product: a.product,
          name: a.product.name,
          value: a.revenue,
          change: a.change,
          note: `${a.priority} · ${a.signal}`,
        }))
        .slice(0, 3),
      rising: ranked.filter((r) => r.product?.level === "新品").slice(0, 3),
      actions: [
        "示例建议：优先复核告警商品的流量入口与转化承接。",
        "示例建议：新品投放前检查数据完整性及评分缺项，不自动调整预算。",
      ],
      window: { from, to: end },
    };
    return wrap(result);
  }
  if (endpoint === "alerts") {
    const selected = alerts.filter((a) => matchProduct(a.product, filters));
    return selected.length ? wrap({ items: selected, total: selected.length }) : wrap(null, true);
  }
  if (endpoint === "diagnosis") {
    const result: Diagnosis = {
      headline: incomplete
        ? "当前比较数据不足，优先补齐经营依据。"
        : "样例交易规模增长，优先观察流量承接与转化效率。",
      evidence: [
        missingAmount
          ? `开发样例：当前范围金额缺失，完整合计不可计算；已知访客 ${visitors}。`
          : `开发样例：当前范围交易金额 ¥${revenue.toFixed(2)}，访客 ${visitors}。`,
        incomplete
          ? "去年同期输入缺失，不生成同比结论。"
          : "样例同比为固定验证值，不代表真实经营趋势。",
      ],
      action: "先核查告警商品，再验证新品投放条件；本卡未调用AI。",
      method: "rule",
      generatedAt: "2026-10-14T18:10:00+08:00",
    };
    return wrap(result);
  }
  if (endpoint === "trend") {
    const dates = unique(rows.map((r) => r.date)).sort();
    const metric = options.metric ?? "revenue",
      comparison = options.comparison ?? "year";
    const metricValue = (selected: DayRow[]) =>
      metric === "revenue"
        ? selected.some((row) => row.revenue === null)
          ? null
          : sumMoney(selected)
        : metric === "visitors"
          ? sumVisitors(selected)
          : metric === "conversion"
            ? 0.043
            : metric === "aov"
              ? 38.5
              : Math.round(
                  sumVisitors(selected) *
                    (metric === "favorites" ? 0.08 : metric === "units" ? 0.048 : 0.043),
                );
    const seriesProducts = activeProducts.slice(0, 3);
    const colors = ["#1768d9", "#d49b2c", "#159675"];
    const points = dates.map((date) => {
      const selected = rows.filter((r) => r.date === date);
      const value = metricValue(selected);
      const point: Trend["points"][number] = {
        date,
        current: value,
        lastYear: incomplete || value === null ? null : value * 0.8,
      };
      if (comparison === "products")
        seriesProducts.forEach((p, i) => {
          point[`p${i}`] = metricValue(selected.filter((r) => r.product.key === p.key));
        });
      if (comparison === "relative") {
        point.revenue = missingAmount ? null : sumMoney(selected) / Math.max(1, revenue);
        point.visitors = sumVisitors(selected) / Math.max(1, visitors);
      }
      return point;
    });
    const series =
      comparison === "products"
        ? seriesProducts.map((p, i) => ({
            key: `p${i}`,
            label: `${p.name} · ${p.shopName}`,
            color: colors[i],
          }))
        : comparison === "relative"
          ? [
              { key: "revenue", label: "交易金额（区间占比）", color: colors[0] },
              { key: "visitors", label: "访客（区间占比）", color: colors[2] },
            ]
          : [
              { key: "current", label: "本期", color: colors[0] },
              { key: "lastYear", label: "去年同期", color: "#9eafc7" },
            ];
    const result: Trend = {
      points,
      series,
      format:
        comparison === "relative"
          ? "percent"
          : metric === "revenue" || metric === "aov"
            ? "money"
            : metric === "conversion"
              ? "percent"
              : "number",
      mode: comparison,
      note:
        comparison === "relative"
          ? "开发样例：以区间总值归一化，展示相对走势，不是实际单位。"
          : "开发样例：仅包含所选范围的合成日期点；缺失值不补零。",
    };
    return wrap(result);
  }
  throw new Error("未知商品样例接口");
}
