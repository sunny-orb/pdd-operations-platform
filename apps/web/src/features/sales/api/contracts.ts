import { z } from "zod";

export const numeric = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/)
  .nullable();
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
export const metaSchema = z.object({
  data_version: z.string().min(1),
  input_versions: z.record(z.string(), z.string()).optional(),
  observed_from: date.nullable(),
  observed_through: date.nullable(),
  completeness_state: z.literal("unknown"),
  complete_through: z.null(),
  source_updated_text: z.string().nullable(),
  source_updated_at: z.null(),
  last_attempt_status: z.string().nullable(),
  last_attempt_at: z.string().nullable(),
  last_success_at: z.string().nullable(),
  request_id: z.string(),
});
export const coverageSchema = z.object({
  latest_observed_date: date.nullable(),
  observed_days: z.number().int(),
  missing_dates: z.array(z.string()),
  field_null_counts: z.record(z.string(), z.number().int()),
  reason_code: z.string(),
  window_start: date,
  window_end: date,
});
export const metricsSchema = z.object({
  shop_revenue: numeric,
  shop_visitors: numeric,
  buyers: numeric,
  orders: numeric,
  buyer_conversion: numeric,
  aov: numeric,
  refund_rate: numeric,
  promotion_spend: numeric,
  promotion_ctr: numeric,
  promotion_roi: numeric,
  refund_match: z
    .object({
      matched_refunds: numeric,
      matched_sales: numeric,
      matched_days: z.number(),
      sales_coverage: numeric,
      excluded_days: z.number(),
      excluded_sales: numeric,
      missing_dates: z.array(z.string()),
      state: z.string(),
      source: z
        .object({
          state: z.string(),
          last_success_at: z.string().nullable(),
          last_attempt_status: z.string().nullable(),
          last_attempt_at: z.string().nullable().optional(),
        })
        .optional(),
    })
    .nullable()
    .optional(),
  promotion_channels: z
    .array(
      z.object({
        code: z.string(),
        spend: numeric,
        revenue: numeric,
        ctr: numeric,
        roi: numeric,
        missing_shop_days: z.number(),
        null_counts: z.record(z.string(), z.number()),
        source: z.object({
          state: z.string(),
          observed_through: z.string().nullable(),
          source_updated_text: z.string().nullable(),
          last_success_at: z.string().nullable().optional(),
          last_attempt_status: z.string().nullable().optional(),
          last_attempt_at: z.string().nullable().optional(),
        }),
      }),
    )
    .optional(),
  coverage: coverageSchema,
  field_states: z.record(z.string(), z.string()),
});
const shopSchema = metricsSchema.extend({
  shop_id: z.string(),
  shop_name: z.string(),
  display_name: z.string(),
  rank: z.number().int().nullable(),
  sales_share: numeric,
});
export const filtersSchema = z.object({
  data: z.object({
    sources: z
      .record(
        z.string(),
        z.object({
          state: z.string(),
          observed_from: z.string().nullable(),
          observed_through: z.string().nullable(),
          source_updated_text: z.string().nullable(),
          last_success_at: z.string().nullable().optional(),
          last_attempt_status: z.string().nullable().optional(),
          last_attempt_at: z.string().nullable().optional(),
        }),
      )
      .optional(),
    shops: z.array(
      z.object({
        source_shop_id: z.string().optional(),
        shop_id: z.string(),
        shop_name: z.string(),
        display_name: z.string(),
      }),
    ),
    months: z.array(z.object({ month: z.string(), date_from: date, date_to: date })).min(1),
    default_month: z.string(),
    recommended_date_from: date,
    recommended_date_to: date,
    calendar_through: date,
    observed_from: date.nullable(),
    observed_through: date.nullable(),
    capabilities: z.record(z.string(), z.boolean()),
  }),
  meta: metaSchema,
});
export const performanceSchema = z.object({ data: metricsSchema, meta: metaSchema });
export const shopsSchema = z.object({
  data: z.object({ items: z.array(shopSchema), missing_shop_days: z.number().int() }),
  meta: metaSchema,
});
export type FiltersResponse = z.infer<typeof filtersSchema>;
export type SalesMetrics = z.infer<typeof metricsSchema>;
export type ShopItem = z.infer<typeof shopSchema>;
export type SalesMeta = z.infer<typeof metaSchema>;
export type Scope = { month: string; from: string; to: string; shop: string };
export type SalesResult = {
  performance: SalesMetrics;
  shops: ShopItem[];
  meta: SalesMeta;
  missingShopDays: number;
  key: string;
};
