import { z } from "zod";

const product = z.object({
  listing_id: z.string(),
  shop_id: z.string(),
  product_id: z.string().optional(),
  name: z.string().nullable(),
  shop_name: z.string().nullable(),
});
export type AssistantProduct = z.infer<typeof product>;
export type PageContext = {
  shop_id?: string;
  date_from?: string;
  date_to?: string;
  product_version?: string;
  promotion_version?: string;
};
export type AssistantScope = {
  page: string;
  subject: AssistantProduct | null;
  context: PageContext;
};
export const sameProduct = (a: AssistantProduct | null, b: AssistantProduct | null) =>
  a?.listing_id === b?.listing_id && a?.shop_id === b?.shop_id;

const coverage = z.object({
  observed_days: z.number(),
  expected_days: z.number(),
  missing_dates: z.array(z.string()),
});
const decimal = z
  .string()
  .regex(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/)
  .nullable();
export const report = z.object({
  kind: z.enum(["product_performance", "product_promotion"]),
  product,
  period: z.object({
    date_from: z.string(),
    date_to: z.string(),
    previous_from: z.string(),
    previous_to: z.string(),
  }),
  metrics: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      unit: z.enum(["currency", "number", "percent", "ratio"]),
      current: decimal,
      previous: decimal,
      change: decimal.optional(),
      status: z.string(),
      reason: z.string().nullable(),
    }),
  ),
  coverage: z.object({
    current: coverage,
    previous: coverage,
    complete_through: z.string().nullable(),
  }),
  data_version: z.string(),
  versions: z.object({ product_version: z.string(), promotion_version: z.string() }).optional(),
  evidence: z.object({
    source: z.string(),
    rules: z
      .object({
        version: z.string(),
        status: z.string(),
        date_from: z.string().nullish(),
        date_to: z.string().nullable(),
        ranking_basis: z.string().optional(),
        baseline_from: z.string().nullish(),
        baseline_to: z.string().nullish(),
        findings: z.array(
          z.object({
            label: z.string().optional(),
            rule: z.string().optional(),
            action: z.string().optional(),
            basis: z.string().optional(),
            evidence: z.string().optional(),
          }),
        ),
        checks: z
          .array(
            z.object({
              key: z.string(),
              label: z.string(),
              state: z.string(),
              reason: z.string().nullable(),
            }),
          )
          .optional(),
      })
      .optional(),
  }),
  series: z.array(z.object({ date: z.string(), values: z.record(z.string(), decimal) })),
});
export type AssistantReport = z.infer<typeof report>;
export const chatAnswer = z.object({
  answer: z.string().trim().min(1),
  status: z.enum(["answered", "need_selection", "no_evidence"]),
  model: z.string(),
  evidence: z.string(),
  subject: product.nullable(),
  candidates: z.array(product),
  reports: z.array(report),
});
export type ChatAnswer = z.infer<typeof chatAnswer>;
