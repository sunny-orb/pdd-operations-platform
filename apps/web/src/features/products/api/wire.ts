import { z } from "zod";
import type { Envelope, Product, Score, Scalar, Meta } from "./contracts";
import { productKey } from "../state/filters";
import { displayChange } from "../components/display";
import { reasonList } from "../components/reasons";

const text = z.string();
const nullableText = text.nullable();
const count = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const decimal = text.max(2000).regex(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/);
const nullableDecimal = decimal.nullable();
const scalar = z.union([decimal, count]).nullable();
const date = z.iso.date();
const windowSchema = z.object({ start_date: date, end_date: date });
const reasons = z.array(text);
const object = z.record(text, z.unknown());
const coverage = z
  .object({
    expected_days: count,
    observed_days: count,
    not_imported_dates: z.array(date),
    missing_record_dates: z.array(date),
    missing_product_dates: z.array(z.unknown()),
  })
  .passthrough();
const coverageView = (c: z.infer<typeof coverage>) => ({
  expectedDays: c.expected_days,
  observedDays: c.observed_days,
  notImportedDays: c.not_imported_dates.length,
  missingRecordDays: c.missing_record_dates.length,
  missingProductCount: c.missing_product_dates.length,
});
const profile = z
  .object({
    platform_code: text.optional(),
    shop_id: text,
    product_id: text,
    product_name: nullableText,
    shop_name: nullableText.optional(),
    category: nullableText.optional(),
    level: nullableText.optional(),
    operator: nullableText.optional(),
    profile_match_status: text.optional(),
    field_statuses: z.record(text, text).optional(),
    listed_date: nullableText.optional(),
  })
  .passthrough();
const metric = z
  .object({
    key: text,
    label: text,
    value: nullableDecimal,
    previous: nullableDecimal,
    year_ago: nullableDecimal,
    mom_change: nullableDecimal,
    yoy_change: nullableDecimal,
    unit: z.enum(["ratio", "yuan", "count"]),
    status: text,
    missing_reasons: reasons,
    missing_value_count: count,
    observed_record_count: count,
  })
  .passthrough();
const risk = z
  .object({
    id: text,
    product: profile,
    priority: z.enum(["P0", "P1", "P2"]),
    risk_title: text,
    current_value: nullableDecimal,
    baseline_value: nullableDecimal,
    change_rate: nullableDecimal,
    trend: z.array(scalar),
    trend_metric: text,
    period_revenue: nullableDecimal,
    estimated_impact_amount: nullableDecimal,
    reason_tags: reasons,
    suggested_action: text,
    due_at: nullableText,
    handling_status: text,
    impact_missing_reasons: reasons,
    priority_status: text,
  })
  .passthrough();
const ranking = z.object({
  product: profile,
  product_revenue: nullableDecimal,
  previous_revenue: nullableDecimal,
  change: nullableDecimal,
  buyer_conversion_rate: nullableDecimal,
  conversion_status: text,
  status: text,
});
const category = z.object({
  label: text,
  category: nullableText,
  product_revenue: nullableDecimal,
  change: nullableDecimal,
  status: text,
  missing_reasons: reasons,
});
const card = z
  .object({
    score_id: text,
    platform_code: text,
    shop_id: text,
    product_id: text,
    product_name: text,
    shop_name: nullableText,
    category: nullableText,
    operator: nullableText,
    profile_match_status: text,
    field_statuses: z.record(text, text),
    as_of_date: date,
    score_status: z.enum(["complete", "partial", "unscored"]),
    verified_score: nullableDecimal,
    complete_score: nullableDecimal,
    verified_weight: count,
    pending_weight: count,
    active_signal_count: count,
    pending_signal_count: count,
    met_signal_count: count,
    decision: z.object({ code: text, label: text, reasons, execution_note: nullableText }),
    current_values: z.record(text, scalar),
    ad_impressions_7d: scalar,
    ad_spend_7d: scalar,
    model_category: nullableText,
    season: nullableText,
    lifecycle: nullableText,
    reference_context_basis: text,
    threshold_status: text,
  })
  .passthrough();
const signal = z.object({
  metric_key: text,
  label: text,
  unit: text,
  weight: count,
  current_value: nullableDecimal,
  points: nullableDecimal,
  tier: nullableText,
  status: text,
  missing_reasons: reasons,
  reference_group: nullableText,
  reference_sample_count: count.nullable(),
  fallback_level: nullableText,
  thresholds: z.object({
    basic: nullableDecimal,
    good: nullableDecimal,
    excellent: nullableDecimal,
    outstanding: nullableDecimal,
  }),
});
export const wireSchemas = {
  filters: z.object({
    shops: z.array(z.object({ shop_id: text, shop_name: nullableText })),
    operators: z.array(text),
    categories: z.array(text),
    levels: z.array(text),
    products: z.array(profile),
    product_total: count,
    page: count,
    page_size: count,
    recommended_window: windowSchema,
    profile_basis: text,
  }),
  scoreFilters: z.object({
    scope: z.literal("scores"),
    as_of_date: date,
    shops: z.array(text),
    operators: z.array(text),
    categories: z.array(text),
    decisions: z.array(z.object({ code: text, label: text })),
    products: z.array(profile),
    product_total: count,
    page: count,
    page_size: count,
  }),
  overview: z.object({
    metrics: z.array(metric).length(7),
    periods: z.array(
      z
        .object({
          days: count,
          value: nullableDecimal,
          mom_change: nullableDecimal,
          window: windowSchema,
          status: text,
          missing_reasons: reasons,
          coverage,
          previous_coverage: coverage,
        })
        .passthrough(),
    ),
    funnel: z.array(
      z.object({ key: text, label: text, value: scalar, status: text, missing_reasons: reasons }),
    ),
    health: z.object({ status: text, title: text, description: text }),
    matched_record_count: count,
    windows: z.object({ current: windowSchema }).passthrough(),
    coverage: object,
  }),
  brief: z.object({
    categories: z.array(category),
    unassigned_category: category.nullable(),
    products: z.array(ranking),
    rising: z.array(ranking),
    declining: z.array(risk),
    actions: z.array(text),
    action_generation_status: text,
    window: windowSchema,
    previous_window: windowSchema,
  }),
  alerts: z.object({
    items: z.array(risk),
    total: count,
    page: count,
    page_size: count,
    unassessed_count: count,
    ranking_status: text,
  }),
  diagnosis: z.object({
    headline: text,
    evidence: z.array(
      z.object({
        metric_key: text,
        label: text,
        comparison: text,
        change: nullableDecimal,
        status: text,
      }),
    ),
    action: text,
    generation_method: z.enum(["rules"]),
    generated_at: text,
    missing_reasons: reasons,
    selected_metric: z.object({ key: text, label: text }).passthrough(),
  }),
  days: z.object({
    items: z.array(
      profile.extend({
        business_date: date,
        key: text,
        visitors: scalar,
        product_revenue: nullableDecimal,
        buyer_conversion_rate: nullableDecimal,
        yoy_change: nullableDecimal,
        conversion_status: text,
      }),
    ),
    total: count,
    page: count,
    page_size: count,
  }),
  scores: z.object({
    items: z.array(card),
    total: count,
    page: count,
    page_size: count,
    as_of_date: date,
    summary: z.object({
      total_count: count,
      filtered_count: count,
      scored_count: count,
      complete_count: count,
      partial_count: count,
      unscored_count: count,
    }),
  }),
  score: card.extend({
    signals: z.array(signal).length(10),
    model_id: text,
    rule_version: text,
    context: z
      .object({
        listed_date: nullableText,
        age_days: count.nullable(),
        first_product_observed_date: nullableText,
        age_basis: text,
        lifecycle_status: text,
        threshold_precision: text,
        evidence_reasons: reasons,
        current_window: windowSchema,
        previous_window: windowSchema,
        coverage: object,
      })
      .passthrough(),
    inputs: object,
    threshold_provenance: object,
  }),
  trend: z.union([
    z.object({
      mode: z.literal("year"),
      metric_key: text,
      note: text,
      points: z.array(
        z.object({
          date,
          value: scalar,
          year_ago: scalar,
          year_ago_date: date.nullable(),
          missing_value_count: count,
        }),
      ),
    }),
    z.object({
      mode: z.enum(["products", "relative"]),
      metric_key: text,
      series: z.array(
        z.object({
          key: text,
          product: profile,
          baseline_date: date.nullable(),
          points: z.array(z.object({ date, value: scalar })),
        }),
      ),
    }),
  ]),
};
const meta = z
  .object({
    data_version: text,
    completeness: z.enum(["unknown", "partial", "complete"]),
    complete_through: date.nullable(),
    profile_basis: text,
    input_batches: z.record(text, text),
    is_preview: z.literal(false),
    data_through: date.nullable().optional(),
    as_of_date: date.optional(),
    last_success_at: nullableText.optional(),
    computed_at: nullableText.optional(),
    score_version: text.optional(),
    model_status: text.optional(),
    has_compatible_result: z.boolean().optional(),
    sync_status: text.optional(),
    last_error: z.object({ code: nullableText, message: nullableText }).nullable().optional(),
    missing_reasons: reasons,
    source_updated_at: nullableText.optional(),
    source_update_status: text.optional(),
    coverage: z
      .object({
        expected_days: count,
        observed_days: count,
        not_imported_dates: z.array(date),
        missing_record_dates: z.array(date),
        missing_product_dates: z.array(z.unknown()),
      })
      .passthrough()
      .optional(),
    last_attempt_at: nullableText.optional(),
    last_attempt_status: nullableText.optional(),
    request_id: text.optional(),
  })
  .passthrough();
const formatOf = (unit: string): "money" | "number" | "percent" | "ratio" =>
  unit === "ratio"
    ? "percent"
    : unit === "yuan" || unit === "yuan_per_visitor"
      ? "money"
      : unit === "multiple"
        ? "ratio"
        : "number";
const metricKeys: Record<string, string> = {
  product_revenue: "revenue",
  visitors: "visitors",
  favorites: "favorites",
  buyers: "buyers",
  units: "units",
  revenue_per_buyer: "aov",
  buyer_conversion_rate: "conversion",
  views: "views",
};
function product(p: z.infer<typeof profile>): Product {
  const label = (field: "category" | "operator" | "level") =>
    p.field_statuses?.[field] === "conflict" ? "资料冲突" : (p[field] ?? "未匹配");
  return {
    shopId: p.shop_id,
    shopName: p.shop_name ?? p.shop_id,
    id: p.product_id,
    name: p.product_name ?? p.product_id,
    styleCode: "",
    category: label("category"),
    level: label("level"),
    operator: label("operator"),
    key: productKey(p.shop_id, p.product_id),
  };
}
function score(c: z.infer<typeof card>): Score {
  return {
    id: c.score_id,
    product: product({ ...c, level: "新品" }),
    date: c.as_of_date,
    modelVersion: "",
    stage: c.lifecycle ? `历史参照：${c.lifecycle}` : "当前阶段未知",
    season: c.season ?? "参照季节未知",
    age: null,
    status: c.score_status,
    score: c.complete_score,
    verifiedScore: c.verified_score,
    pendingWeight: c.pending_weight,
    activeCount: c.active_signal_count,
    pendingCount: c.pending_signal_count,
    metCount: c.met_signal_count,
    recommendation: c.decision.label,
    reason: reasonList([
      ...c.decision.reasons,
      ...(c.threshold_status !== "fixed_display_trial"
        ? [c.threshold_status]
        : ["thresholds_trial_or_not_validated"]),
    ]).join("；"),
    metrics: [],
    currentValues: { ...c.current_values, ad_impressions_7d: c.ad_impressions_7d },
    referenceNote: "固定历史参照试算；参照季节和阶段不代表当前实际分类",
  };
}
const riskItem = (r: z.infer<typeof risk>) => ({
  id: r.id,
  product: product(r.product),
  priority: r.priority,
  signal: r.risk_title,
  current: r.current_value,
  baseline: r.baseline_value,
  change: r.change_rate,
  trend: r.trend,
  revenue: r.period_revenue,
  impact: r.estimated_impact_amount,
  metricFormat: r.trend_metric.includes("conversion") ? ("percent" as const) : ("number" as const),
  reasons: [
    ...r.reason_tags,
    ...reasonList(r.impact_missing_reasons),
    ...(r.priority_status === "upgrade_not_verified" ? ["优先级升级依据不足"] : []),
  ],
  action: r.suggested_action,
  dueAt: r.due_at,
  workflowState: r.handling_status === "not_enabled" ? null : r.handling_status,
});
function present<S>(
  schema: z.ZodType<S>,
  value: unknown,
  convert: (data: S) => unknown,
): Envelope<unknown> {
  const v = z
    .object({
      data: schema.nullable(),
      availability: z.enum(["ready", "empty", "not_connected"]),
      computation_state: z.enum(["ready", "pending", "running", "failed"]),
      meta,
    })
    .parse(value);
  if (v.availability === "ready" && v.computation_state === "ready" && v.data === null)
    throw new Error("ready without data");
  const m = v.meta;
  const metadata: Meta = {
    dataVersion: m.data_version,
    dataThrough: m.data_through ?? m.as_of_date ?? null,
    lastSuccessAt: m.last_success_at ?? m.computed_at ?? null,
    completeness: m.completeness,
    missingReasons: reasonList(m.missing_reasons),
    isPreview: false,
    completeThrough: m.complete_through,
    scoreVersion: m.score_version,
    hasCompatibleResult: m.has_compatible_result,
    modelStatus: m.model_status,
    syncStatus: m.sync_status,
    lastError: m.last_error,
    sourceUpdatedAt: m.source_updated_at,
    sourceUpdateStatus: m.source_update_status,
    requestId: m.request_id,
    lastAttemptAt: m.last_attempt_at,
    lastAttemptStatus: m.last_attempt_status,
    coverageNote: m.coverage
      ? `范围${m.coverage.expected_days}天，已观察${m.coverage.observed_days}天；未入库${m.coverage.not_imported_dates.length}天，缺店铺记录${m.coverage.missing_record_dates.length}天，缺商品记录${m.coverage.missing_product_dates.length}项。`
      : undefined,
  };
  return {
    data: v.data === null ? null : convert(v.data),
    availability: v.availability,
    computationState: v.computation_state,
    meta: metadata,
  };
}
/** 正式响应先逐端点校验，再映射展示结构；不提供旧线上/样例API兼容或失败回退。 */
export function parseApiEnvelope<T = unknown>(endpoint: string, value: unknown): Envelope<T> {
  try {
    let result: Envelope<unknown>;
    if (endpoint === "filters") {
      const scope = (value as { data?: { scope?: string } })?.data?.scope;
      result =
        scope === "scores"
          ? present(wireSchemas.scoreFilters, value, (d) => ({
              shops: d.shops.map((id) => ({ value: id, label: id })),
              operators: d.operators,
              categories: d.categories,
              levels: [],
              products: d.products.map(product),
              productTotal: d.product_total,
              dateRange: { from: d.as_of_date, to: d.as_of_date },
              retainSelection: true,
              decisions: d.decisions.map((x) => ({ value: x.code, label: x.label })),
            }))
          : present(wireSchemas.filters, value, (d) => ({
              shops: d.shops.map((s) => ({ value: s.shop_id, label: s.shop_name ?? s.shop_id })),
              operators: d.operators,
              categories: d.categories,
              levels: d.levels,
              products: d.products.map(product),
              productTotal: d.product_total,
              dateRange: {
                from: d.recommended_window.start_date,
                to: d.recommended_window.end_date,
              },
              retainSelection: true,
            }));
    } else if (endpoint === "overview")
      result = present(wireSchemas.overview, value, (d) => ({
        metrics: d.metrics.map((m) => ({
          key: metricKeys[m.key],
          label: m.label,
          value: m.value,
          previous: m.previous,
          yearAgo: m.year_ago,
          mom: m.mom_change,
          yoy: m.yoy_change,
          format: formatOf(m.unit),
          status: m.status,
          missingReasons: reasonList(m.missing_reasons),
        })),
        periods: d.periods.map((p) => ({
          days: p.days,
          value: p.value,
          change: p.mom_change,
          status: p.status,
          missingReasons: reasonList(p.missing_reasons),
          coverage: coverageView(p.coverage),
          previousCoverage: coverageView(p.previous_coverage),
          window: `${p.window.start_date} 至 ${p.window.end_date}`,
        })),
        funnel: d.funnel.map((n) => ({
          key: n.key,
          label: n.label,
          value: n.value,
          note: reasonList(n.missing_reasons).join("；"),
        })),
        health: {
          title: d.health.title,
          description: d.health.description,
          tone: d.health.status === "insufficient_evidence" ? "info" : "warning",
        },
      }));
    else if (endpoint === "brief")
      result = present(wireSchemas.brief, value, (d) => {
        const rank = (r: z.infer<typeof ranking>) => ({
          product: product(r.product),
          name: r.product.product_name ?? r.product.product_id,
          value: r.product_revenue,
          change: r.change,
          conversion: r.buyer_conversion_rate,
          note: `${r.product.shop_name ?? r.product.shop_id} · ${r.product.level ?? "等级未匹配"}${r.status === "partial" ? " · 已观察小计" : ""}`,
        });
        const cat = (r: z.infer<typeof category>) => ({
          name: r.label,
          value: r.product_revenue,
          change: r.change,
          note: reasonList(r.missing_reasons).join("；"),
        });
        return {
          categories: d.categories.map(cat),
          products: d.products.map(rank),
          rising: d.rising.map(rank),
          declining: d.declining.map((r) => ({
            product: product(r.product),
            name: r.product.product_name ?? r.product.product_id,
            value: r.period_revenue,
            change: null,
            note: `${r.priority} · ${r.risk_title} · 累计所选期间交易`,
          })),
          actions: d.actions,
          unassignedCategory: d.unassigned_category ? cat(d.unassigned_category) : null,
          window: { from: d.window.start_date, to: d.window.end_date },
        };
      });
    else if (endpoint === "scores")
      result = present(wireSchemas.scores, value, (d) => ({
        items: d.items.map(score),
        total: d.total,
        counts: {
          complete: d.summary.complete_count,
          partial: d.summary.partial_count,
          unscored: d.summary.unscored_count,
        },
        allCount: d.summary.total_count,
      }));
    else if (endpoint.startsWith("scores/"))
      result = present(wireSchemas.score, value, (d) => ({
        ...score(d),
        age: d.context.age_days,
        listedDate: d.context.listed_date,
        observedDate: d.context.first_product_observed_date,
        modelVersion: d.model_id,
        ruleVersion: d.rule_version,
        currentWindow: `${d.context.current_window.start_date} 至 ${d.context.current_window.end_date}`,
        previousWindow: `${d.context.previous_window.start_date} 至 ${d.context.previous_window.end_date}`,
        metrics: d.signals.map((s) => ({
          key: s.metric_key,
          label: s.label,
          value: s.current_value,
          format: formatOf(s.unit),
          weight: s.weight,
          tiers: [
            s.thresholds.basic,
            s.thresholds.good,
            s.thresholds.excellent,
            s.thresholds.outstanding,
          ],
          grade: s.tier
            ? ({
                basic: "基础",
                good: "良好",
                excellent: "优秀",
                outstanding: "突出",
                below_basic: "未达基础",
              }[s.tier] ?? "待验证")
            : "待验证",
          points: s.points,
          reference: s.reference_group ?? "尚无适用参照",
          sampleSize: s.reference_sample_count,
          reason: reasonList(s.missing_reasons).join("；"),
        })),
      }));
    else if (endpoint === "days")
      result = present(wireSchemas.days, value, (d) => ({
        items: d.items.map((r) => ({
          key: r.key,
          product: product(r),
          date: r.business_date,
          visitors: r.visitors,
          revenue: r.product_revenue,
          conversion: r.buyer_conversion_rate,
          yoy: r.yoy_change,
        })),
        total: d.total,
      }));
    else if (endpoint === "alerts")
      result = present(wireSchemas.alerts, value, (d) => ({
        items: d.items.map(riskItem),
        total: d.total,
        unassessedCount: d.unassessed_count,
        rankingStatus: d.ranking_status,
      }));
    else if (endpoint === "diagnosis")
      result = present(wireSchemas.diagnosis, value, (d) => ({
        headline: `${d.selected_metric.label} · ${d.headline}`,
        action: d.action,
        evidence: d.evidence.map(
          (e) =>
            `${e.label} · ${e.comparison === "year_ago" ? "同比" : "环比"} ${displayChange(e.change)}`,
        ),
        method: "rule",
        generatedAt: d.generated_at,
      }));
    else if (endpoint === "trend")
      result = present(wireSchemas.trend, value, (d) => {
        const format = d.metric_key.includes("rate")
          ? "percent"
          : d.metric_key.includes("revenue")
            ? "money"
            : "number";
        if (d.mode === "year")
          return {
            points: d.points.map((p) => ({ date: p.date, current: p.value, lastYear: p.year_ago })),
            series: [
              { key: "current", label: "当前", color: "#1768d9" },
              { key: "lastYear", label: "去年同期", color: "#6c91bc" },
            ],
            format,
            mode: d.mode,
            note: d.note,
          };
        const colors = ["#1768d9", "#118362", "#d78d17", "#c33443", "#7355c9"];
        const points = new Map<string, Record<string, Scalar>>();
        d.series.forEach((s) =>
          s.points.forEach((p) => {
            const row = points.get(p.date) ?? { date: p.date, current: null, lastYear: null };
            row[s.key] = p.value;
            points.set(p.date, row);
          }),
        );
        return {
          points: [...points.values()].sort((a, b) => String(a.date).localeCompare(String(b.date))),
          series: d.series.map((s, i) => ({
            key: s.key,
            label: `${s.product.product_name ?? s.product.product_id} · ${s.product.shop_name ?? s.product.shop_id}`,
            color: colors[i % colors.length],
          })),
          format,
          mode: d.mode,
          note:
            d.mode === "relative"
              ? "各商品首个正值为100%，缺值不补零"
              : "按店铺商品身份比较，缺值不补零",
        };
      });
    else throw new Error("unknown endpoint");
    return result as Envelope<T>;
  } catch (error) {
    throw new Error(`商品接口格式不符合约定（${endpoint}），请核对字段与数据类型。`, {
      cause: error,
    });
  }
}
