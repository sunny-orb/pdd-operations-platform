import { useEffect, useState } from "react";
import { z } from "zod";
import { ApiError, getJson } from "../../../shared/api/client";
import { metaSchema, numeric } from "./contracts";
export const sourceSchema = z.object({
  state: z.string(),
  observed_from: z.string().nullable(),
  observed_through: z.string().nullable(),
  source_updated_text: z.string().nullable(),
  last_success_at: z.string().nullable(),
  last_attempt_status: z.string().nullable(),
  last_attempt_at: z.string().nullable().optional(),
});
export const targetsSchema = z.object({
  data: z.object({
    month_missing_days: z.number().optional(),
    annual_missing_days: z.number().optional(),
    month_actual: numeric,
    annual_actual: numeric,
    month_target: numeric,
    annual_target: numeric,
    month_ratio: numeric,
    annual_ratio: numeric,
    gap: numeric,
    state: z.string(),
    target_basis: z.literal("original_gmv").optional(),
    target_basis_label: z.string().optional(),
    target_coverage: z
      .object({
        available_months: z.array(z.string()),
        missing_months: z.array(z.string()),
        member_shop_ids: z.array(z.string()),
        scope_state: z.string(),
      })
      .optional(),
    target_source: z
      .object({
        state: z.string(),
        url: z.string(),
        version: z.string().nullable(),
        field_id: z.string(),
        last_success_at: z.string().nullable(),
        last_attempt_status: z.string().nullable(),
        last_attempt_at: z.string().nullable(),
      })
      .optional(),
    monthly_window: z.object({ start: z.string(), end: z.string() }).optional(),
    annual_window: z.object({ start: z.string(), end: z.string() }).optional(),
    note: z.string().optional(),
  }),
  meta: metaSchema,
});
const ratingPoint = z.object({
  shop_id: z.string(),
  date: z.string().nullable(),
  raw_score: numeric,
  score: numeric,
  state: z.string(),
  source_created_time_raw: z.string().nullable(),
});
export const ratingsSchema = z.object({
  data: z.object({
    latest_date: z.string().nullable(),
    latest: z.array(ratingPoint),
    points: z.array(ratingPoint),
    source: sourceSchema,
  }),
  meta: metaSchema,
});
export const regionSchema = z.object({
  data: z.object({
    items: z.array(
      z.object({
        region: z.string(),
        transaction_amount: numeric.optional(),
        transaction_orders: numeric.optional(),
        transaction_buyers: numeric.optional(),
        aov: numeric.optional(),
        actual_shipped_amount: numeric.optional(),
        actual_shipped_qty: numeric.optional(),
        actual_returned_amount: numeric.optional(),
        actual_returned_qty: numeric.optional(),
        refund_rate: numeric.optional(),
        share: numeric,
        null_counts: z.record(z.string(), z.number()),
      }),
    ),
    totals: z.record(z.string(), numeric),
    region_count: z.number(),
    null_counts: z.record(z.string(), z.number()),
    missing_shop_days: z.array(z.string()),
    field_gaps: z
      .array(
        z.object({
          shop_id: z.string(),
          date: z.string(),
          fields: z.record(z.string(), z.number().int().nonnegative()),
        }),
      )
      .optional(),
    source: sourceSchema,
  }),
  meta: metaSchema,
});
export const productFiltersSchema = z.object({
  data: z.object({
    categories: z.array(z.string()),
    products: z.array(
      z.object({ key: z.string(), category: z.string(), code: z.string(), name: z.string() }),
    ),
    total: z.number(),
    offset: z.number(),
    limit: z.number(),
    source: sourceSchema,
  }),
  meta: metaSchema,
});
/** 独立区块只展示当前参数同版本结果；版本变化交由页面统一有界恢复。 */
export function useSalesDetail<T extends { meta: { data_version: string } }>(
  path: string | null,
  schema: z.ZodType<T>,
  onConflict: () => void,
  statusKey?: string,
) {
  const [stored, setStored] = useState<{ path: string; value: T } | null>(null);
  const [error, setError] = useState<{ path: string; text: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    void getJson(path, controller.signal)
      .then((v) => {
        const value = schema.parse(v);
        if (
          value.meta.data_version !==
          new URL(path, window.location.origin).searchParams.get("data_version")
        )
          throw new ApiError("数据版本变化", 409);
        if (!controller.signal.aborted) {
          setStored({ path, value });
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 409) onConflict();
        else setError({ path, text: "本区块读取失败，请重试；其余区块仍可查看。" });
      });
    return () => controller.abort();
  }, [path, schema, onConflict, refresh, statusKey]);
  return {
    data: stored?.path === path ? stored.value : null,
    error: error?.path === path ? error.text : null,
    retry: () => setRefresh((n) => n + 1),
  };
}
export function detailPath(
  endpoint: string,
  version: string | undefined,
  params: Record<string, string>,
) {
  return version
    ? `/api/v1/sales/${endpoint}?${new URLSearchParams({ data_version: version, ...params })}`
    : null;
}
