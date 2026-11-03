import { supplementSchema } from "../../shared/promotion/contracts";
import { z } from "zod";
export const metric = z.object({
  value: z.string().nullable(),
  status: z.string(),
  reason: z.string().nullable(),
  label: z.string(),
  type: z.string(),
  previous: z.string().nullable().optional(),
  change: z.string().nullable().optional(),
  comparison_status: z
    .enum([
      "available",
      "reference",
      "no_current",
      "no_previous",
      "zero_base",
      "invalid_base",
      "history_short",
    ])
    .nullish(),
  business_date_basis: z.literal("payment_date").nullish(),
  attribution_state: z.literal("source_reported_payment_date").nullish(),
});
export type Metric = z.infer<typeof metric>;
export const metricMap = z.record(z.string(), metric);
export const source = z.object({}).catchall(z.unknown());
export const metadata = z.object({
  mode: z.enum(["store", "product"]),
  date_from: z.string().nullable(),
  date_to: z.string().nullable(),
  recommended_from: z.string().nullable(),
  recommended_to: z.string().nullable(),
  observed_rows: z.string(),
  complete_through: z.string().nullable(),
  completeness: z.string(),
  business_date_basis: z.literal("payment_date").optional(),
  attribution_state: z.literal("source_reported_payment_date").optional(),
  attribution_window_days: z.null().optional(),
  sources: z.array(source),
  historical_supplements: z.array(supplementSchema).optional(),
});
const option = z.object({ value: z.string(), label: z.string() });
const product = option.extend({
  listing_id: z.string(),
  product_id: z.string(),
  shop_id: z.string(),
  product_name: z.string().nullable(),
  category: z.string().nullable().optional(),
  level: z.string().nullable().optional(),
  level_details: z.array(z.string()).optional(),
  operator: z.string().nullable().optional(),
  search_names: z.array(z.string()).optional(),
});
export const options = z.object({
  shops: z.array(option),
  channels: z.array(option),
  products: z.array(product),
  selected_products: z.array(product),
  categories: z.array(z.string()),
  levels: z.array(z.string()),
  operators: z.array(z.string()),
  total: z.number(),
});
export const missing = z.object({}).catchall(z.unknown());
export const coverage = z
  .object({
    stale: z.boolean().optional(),
    observed_through: z.string().nullable().optional(),
    covered_days: z.number().optional(),
    observed_days: z.number().optional(),
    missing_dates: z.array(z.string()).optional(),
    missing_truncated: z.boolean().optional(),
    missing_count: z.number().optional(),
    missing: z.array(missing).optional(),
    completeness: z.string().optional(),
    complete_through: z.string().nullable().optional(),
  })
  .catchall(z.unknown());
const roiExclusions = z.object({
  missing_spend_rows: z.number(),
  missing_revenue_rows: z.number(),
  revenue_without_spend: z.string(),
});
export const overview = z.object({
  roi_exclusions: roiExclusions.nullish(),
  historical_supplements: z.array(supplementSchema).optional(),
  metrics: metricMap,
  net: metricMap,
  series: z.array(
    z.object({
      date: z.string(),
      metrics: metricMap,
      previous_date: z.string().nullable(),
      previous: metricMap,
      missing: z.boolean(),
    }),
  ),
  funnel: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      value: z.string().nullable(),
      status: z.string(),
    }),
  ),
  top5: z.array(
    z.object({
      date: z.string(),
      roi: z.string().nullable(),
      spend: z.string().nullable(),
      revenue: z.string().nullable(),
      roi_status: z.string(),
      roi_exclusions: roiExclusions,
    }),
  ),
  coverage,
  current_record_count: z.string(),
  dataset_record_count: z.string(),
  windows: z.array(z.object({ role: z.string(), start: z.string(), end: z.string() })),
  channel_summaries: z.array(
    z.object({ channel: z.string(), label: z.string(), metrics: metricMap, net: metricMap }),
  ),
});
export type Overview = z.infer<typeof overview>;
export const day = z.object({
  date: z.string(),
  shop_id: z.string().optional(),
  shop_name: z.string().optional(),
  channel: z.string().optional(),
  channel_label: z.string().optional(),
  source: z.string().optional(),
  source_label: z.enum(["数仓", "飞书", "人工确认"]).optional(),
  spend: z.string().nullable(),
  revenue: z.string().nullable(),
  net_revenue: z.string().nullable(),
  deals: z.string().nullable(),
  impressions: z.string().nullable(),
  clicks: z.string().nullable(),
  roi: z.string().nullable(),
  ctr: z.string().nullable(),
  cvr: z.string().nullable(),
});
export const days = z.object({
  items: z.array(day),
  total: z.number(),
  page: z.number(),
  size: z.number(),
  total_pages: z.number(),
  row_grain: z.string(),
});
export type Days = z.infer<typeof days>;
const finding = z.object({
  level: z.string(),
  rule: z.string(),
  evidence: z.string(),
  action: z.string(),
  status: z.string(),
});
const clue = z.object({
  label: z.string(),
  current: z.string().nullable(),
  previous: z.string().nullable(),
  change: z.string().nullable(),
  evidence: z.string(),
  action: z.string(),
  status: z.string(),
});
const result = z.object({
  current: metricMap,
  previous: metricMap,
  findings: z.array(finding),
  clues: z.array(clue),
  notes: z.array(z.string()),
  priority: z.string(),
  evaluation_state: z.enum(["rated", "partial", "unrated"]),
  rule_results: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      state: z.enum(["triggered", "clear", "not_applicable", "unavailable"]),
      reason: z.string().nullable(),
    }),
  ),
  exclusions: z.object({ current: roiExclusions, previous: roiExclusions }),
  current_days: z.number(),
  previous_days: z.number(),
  current_record_count: z.string(),
  previous_record_count: z.string(),
});
const object = result.extend({
  object_key: z.string(),
  object_type: z.string(),
  label: z.string(),
  listing_id: z.string().nullable(),
  product_id: z.string().nullable(),
  product_name: z.string().nullable(),
  shop_id: z.string().nullable(),
  shop_name: z.string().nullable(),
  category: z.string().nullable(),
  level: z.string().nullable(),
  operator: z.string().nullable(),
  focus: z.boolean(),
  action_context: z.unknown().optional(),
});
export const diagnosis = z.object({
  current_window: z.object({ start: z.string().optional(), end: z.string().optional() }),
  previous_window: z.object({ start: z.string().optional(), end: z.string().optional() }),
  overall: result.nullable(),
  products: z.array(object),
  flagged_products: z.array(object),
  focused_products: z.array(object),
  shops: z.array(object),
  categories: z.array(object),
  counts: z.record(z.string(), z.number()),
  coverage,
  rule_version: z.string(),
  rule_states: z.record(z.string(), z.unknown()),
  status: z.string(),
});
export type DiagnosisData = z.infer<typeof diagnosis>;
export type DiagnosisObject = z.infer<typeof object>;
export const diagnosisObjects = z.object({
  items: z.array(object),
  total: z.number(),
  group_total: z.number(),
  page: z.number(),
  size: z.number(),
  total_pages: z.number(),
});
