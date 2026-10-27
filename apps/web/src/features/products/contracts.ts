import { z } from "zod";
const decimal = z
  .string()
  .regex(/^-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?$/)
  .nullable();
const kind = z.enum(["number", "currency", "percent", "ratio"]);
const base = z
  .object({
    listing_id: z.string(),
    shop_id: z.string(),
    shop_name: z.string().nullable(),
    product_id: z.string(),
    product_name: z.string().nullable(),
    category: z.string().nullable(),
    level: z.string().nullable(),
    operator: z.string().nullable(),
    field_status: z.record(z.string(), z.string()),
  })
  .passthrough();
export const signal = z.object({
  code: z.string(),
  label: z.string(),
  weight: z.number(),
  type: kind,
  value: decimal,
  basic: decimal,
  good: decimal,
  excellent: decimal,
  outstanding: decimal,
  grade: z.string().nullable(),
  points: decimal,
  status: z.string(),
  reason: z.string(),
  reference_group: z.string().nullable(),
  sample_count: z.string().nullable(),
});
export const card = base.extend({
  signals: z.array(signal),
  start: z.string(),
  end: z.string(),
  decision: z.string(),
  decision_label: z.string(),
  score: decimal,
  pending_weight: z.string(),
  complete_score: decimal,
  lifecycle: z.string().nullable(),
  season: z.string().nullable(),
  revenue: decimal,
  visitors: decimal,
  units: decimal,
  ad_impressions: decimal,
  ad_roi: decimal,
  coverage: z.string(),
  reason: z.string(),
  action: z.string(),
});
const metric = z
  .object({
    label: z.string(),
    type: kind,
    value: decimal,
    previous: decimal,
    previous_period: decimal,
    yoy: decimal,
    mom: decimal,
    status: z.string(),
    reason: z.string().nullable(),
  })
  .passthrough();
const alert = z.object({
  label: z.string(),
  priority: z.enum(["P0", "P1", "P2"]),
  score: z.number(),
  current: decimal,
  baseline: decimal,
  change: decimal,
  type: kind,
  trend: z.array(decimal),
  expected_loss: decimal,
  reasons: z.array(z.string()),
  action: z.string(),
  deadline: z.string().nullable(),
  status: z.string(),
  basis: z.string(),
});
const hot = base.extend({
  revenue: decimal,
  previous_revenue: decimal,
  growth: decimal,
  conversion: decimal,
  coverage: z.string(),
});
const risk = hot.extend({ alert, period_revenue: decimal });
export const meta = z.object({
  data_version: z.string(),
  date_from: z.string(),
  date_to: z.string(),
  observed_rows: z.string(),
  product_count: z.string(),
  success_at: z.string().nullable(),
  complete_through: z.string().nullable(),
  completeness: z.string(),
  profile_basis: z.string(),
  latest_attempt: z.object({
    status: z.string().optional(),
    started_at: z.string().nullable().optional(),
    finished_at: z.string().nullable().optional(),
    error_code: z.string().nullable().optional(),
  }),
  sources: z.array(
    z.object({
      dataset: z.string(),
      table: z.string(),
      version: z.string(),
      received_at: z.string().nullable(),
      status: z.string(),
      completeness: z.string(),
    }),
  ),
});
export const board = z.object({
  metrics: z.record(z.string(), metric),
  comparison_series: z.array(
    z.object({
      listing_id: z.string(),
      label: z.string(),
      points: z.array(z.object({ date: z.string(), values: z.record(z.string(), decimal) })),
    }),
  ),
  comparison_mode: z.string(),
  selection: z.object({
    product_name: z.string().nullable(),
    shop_name: z.string().nullable(),
    operator: z.string().nullable(),
    category: z.string().nullable(),
    level: z.string().nullable(),
  }),
  series: z.array(
    z.object({
      date: z.string(),
      values: z.record(z.string(), decimal),
      previous: z.record(z.string(), decimal),
    }),
  ),
  windows: z.array(
    z.object({
      days: z.number(),
      start: z.string(),
      end: z.string(),
      metrics: z.record(
        z.string(),
        z.object({
          value: decimal,
          change: decimal,
          status: z.string(),
          reason: z.string().nullable(),
          comparison_status: z.string(),
        }),
      ),
    }),
  ),
  health: z.object({ title: z.string(), tone: z.string(), detail: z.string() }),
  risks: z.array(risk),
  brief: z.object({
    categories: z.array(z.object({ category: z.string(), revenue: decimal, growth: decimal })),
    products: z.array(hot),
    risks: z.array(risk),
    rising: z.array(hot),
    start: z.string(),
    end: z.string(),
    previous_start: z.string(),
  }),
  funnel: z.array(
    z.object({
      label: z.string(),
      value: decimal,
      status: z.string(),
      reason: z.string().nullable(),
    }),
  ),
  observed_rows: z.string(),
  product_count: z.string(),
  scope: z.object({
    start: z.string(),
    end: z.string(),
    previous_start: z.string().nullable().optional(),
    previous_end: z.string().nullable().optional(),
  }),
  basis: z.string(),
});
export const scorePage = z.object({
  items: z.array(card),
  total: z.number(),
  page: z.number(),
  size: z.number(),
  start: z.string(),
  end: z.string(),
  status: z.string(),
  groups: z.object({ complete: z.number(), pending: z.number(), unscored: z.number() }),
});
export const dayPage = z.object({
  items: z.array(
    base.extend({
      date: z.string(),
      visitors: decimal,
      revenue: decimal,
      conversion: decimal,
      yoy: decimal,
    }),
  ),
  total: z.number(),
  page: z.number(),
  size: z.number(),
  scope: z.object({
    start: z.string(),
    end: z.string(),
    previous_start: z.string().nullable().optional(),
    previous_end: z.string().nullable().optional(),
  }),
});
export const options = z.object({
  selection: z.object({
    operator: z.string(),
    category: z.string(),
    level: z.string(),
    products: z.array(z.string()),
  }),
  selected_products: z.array(z.object({ value: z.string(), label: z.string() })),
  shops: z.array(z.object({ value: z.string(), label: z.string().nullable() })),
  operators: z.array(z.string()),
  categories: z.array(z.string()),
  levels: z.array(z.string()),
  products: z.array(z.object({ value: z.string(), label: z.string() })),
  total: z.number(),
});
export type Board = z.infer<typeof board>;
export type ScoreCard = z.infer<typeof card>;
export type Meta = z.infer<typeof meta>;
export type Options = z.infer<typeof options>;
export type DayPage = z.infer<typeof dayPage>;
