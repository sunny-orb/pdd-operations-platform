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
  domain?:
    | "all"
    | "products"
    | "promotion_store"
    | "promotion_product"
    | "sales"
    | "aftersales"
    | "weather"
    | "data_status";
  versions?: Record<string, string>;
  listing_ids?: string[];
  month?: string;
  report_id?: string;
  category?: string;
  operator?: string;
  level?: string;
  keyword?: string;
  product_key?: string;
  mode?: "store" | "product";
  view?: string;
  focus?: "all" | "size" | "quality";
  shop_id?: string;
  shop_ids?: string[];
  category_values?: string[];
  operator_values?: string[];
  level_values?: string[];
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
const scalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const displayUnit = z.enum(["currency", "number", "percent", "ratio", "text"]);
export const businessReport = z.object({
  kind: z.literal("dashboard"),
  domain: z.string(),
  capability: z.string(),
  title: z.string(),
  source: z.string(),
  period: z.object({ date_from: z.string(), date_to: z.string() }).nullable(),
  scope: z.record(z.string(), z.unknown()),
  data_version: z.string().nullable(),
  versions: z.record(z.string(), z.string().nullable()),
  facts: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      value: scalar,
      unit: displayUnit,
      status: z.string(),
      reason: z.string().nullable(),
    }),
  ),
  tables: z.array(
    z.object({
      title: z.string(),
      columns: z.array(z.object({ key: z.string(), label: z.string(), unit: displayUnit })),
      rows: z.array(z.record(z.string(), scalar)),
      total: z.number().nullable(),
      offset: z.number(),
    }),
  ),
  notes: z.array(z.string()),
  series: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      unit: displayUnit,
      points: z.array(z.object({ date: z.string(), value: scalar })),
    }),
  ),
});
export type BusinessReport = z.infer<typeof businessReport>;
export const chatAnswer = z.object({
  answer: z.string().trim().min(1),
  status: z.enum(["answered", "need_selection", "no_evidence"]),
  model: z.string(),
  evidence: z.string(),
  subject: product.nullable(),
  candidates: z.array(product),
  reports: z.array(z.union([report, businessReport])),
  query_context: z
    .object({
      domain: z
        .enum([
          "all",
          "products",
          "promotion_store",
          "promotion_product",
          "sales",
          "aftersales",
          "weather",
          "data_status",
        ])
        .optional(),
      versions: z.record(z.string(), z.string()),
      date_from: z.string().optional(),
      date_to: z.string().optional(),
      shop_id: z.string().optional(),
      shop_ids: z.array(z.string()).max(20).optional(),
      category_values: z.array(z.string()).max(20).optional(),
      operator_values: z.array(z.string()).max(20).optional(),
      level_values: z.array(z.string()).max(20).optional(),
      month: z.string().optional(),
      report_id: z.string().optional(),
      category: z.string().optional(),
      keyword: z.string().optional(),
      operator: z.string().optional(),
      level: z.string().optional(),
      product_key: z.string().optional(),
      mode: z.enum(["store", "product"]).optional(),
      view: z.string().optional(),
      focus: z.enum(["all", "size", "quality"]).optional(),
    })
    .optional(),
  usage: z
    .object({
      input_tokens: z.number().optional(),
      output_tokens: z.number().optional(),
      requests: z.number().optional(),
      tool_calls: z.number().optional(),
    })
    .optional(),
});
export type ChatAnswer = z.infer<typeof chatAnswer>;
