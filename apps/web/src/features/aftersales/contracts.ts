import { z } from "zod";

export const decimal = z
  .string()
  .regex(/^-?\d+(?:\.\d+)?$/)
  .nullable();
const nullableText = z.string().nullable();
export const report = z.object({
  id: z.string(),
  report_type: z.enum(["weekly", "monthly", "custom"]),
  label: z.string(),
  period_start: z.string(),
  period_end: z.string(),
  generated_at: nullableText,
  document_url: nullableText,
  sheet_url: nullableText,
});
export const source = z.object({
  code: z.string(),
  label: z.string(),
  locator: nullableText,
  status: z.string(),
  observed_from: nullableText,
  observed_to: nullableText,
  last_success_at: nullableText,
  last_attempt_at: nullableText,
  error_code: nullableText,
  note: z.string(),
});
export const metadata = z.object({
  data_version: nullableText,
  status: z.enum(["ready", "partial", "source_unavailable"]),
  default_report_id: nullableText,
  observed_to: nullableText,
  complete_through: nullableText,
  last_success_at: nullableText,
  last_attempt_at: nullableText,
  sources: z.array(source),
});
export const options = z.object({
  reports: z.array(report),
  categories: z.array(z.string()),
  note: z.string(),
});
export const issue = z.object({ name: z.string(), qty: decimal, share: decimal });
export const metric = z.object({
  key: z.string(),
  label: z.string(),
  type: z.enum(["number", "currency", "percent", "points"]),
  value: decimal,
  detail: z.string(),
  note: z.string(),
  status: z.string(),
});
export const priority = z.object({
  id: z.string(),
  kind: z.string(),
  title: z.string(),
  detail: z.string(),
  verification: z.string(),
});
export const risk = z.object({
  id: z.string(),
  name: z.string(),
  amount: decimal,
  amount_share: decimal,
  total_refund_rate: decimal,
  change_pp: decimal,
  risk_score: decimal,
  note: z.string(),
});
export const action = z.object({
  id: z.string(),
  status: z.string(),
  object: z.string(),
  amount: decimal,
  amount_share: decimal,
  risk_score: decimal,
  hypothesis: z.string(),
  action: z.string(),
  verification: z.string(),
  previous_rate: decimal,
  current_rate: decimal,
  note: z.string(),
});
export const stage = z.object({
  name: z.string(),
  amount: decimal,
  qty: decimal,
  share: decimal,
  trend: z.string(),
  status: z.string(),
  note: z.string(),
});
export const overview = z.object({
  report: report.nullable(),
  metrics: z.array(metric),
  risk_metrics: z.array(metric),
  decision: z.object({
    title: z.string(),
    status: z.string(),
    rate: decimal,
    amount: decimal,
    amount_share: decimal,
    change_pp: decimal,
    note: z.string(),
  }),
  priorities: z.array(priority),
  actions: z.array(action),
  stages: z.array(stage),
  size_breakdown: z.array(issue),
  reasons: z.array(issue),
  trend: z.array(
    z.object({
      id: z.string(),
      label: z.string(),
      period_start: z.string(),
      period_end: z.string(),
      qty: decimal,
    }),
  ),
  scope_note: z.string(),
  quality_note: z.string(),
  core_insights: z.array(z.object({ label: z.string(), title: z.string(), detail: z.string() })),
  category_risks: z.array(risk),
  style_risks: z.array(risk),
  category_actions: z.array(priority),
  style_actions: z.array(priority),
});
export const analysis = z.object({
  status: z.string(),
  scope_note: z.string(),
  columns: z.array(
    z.object({
      key: z.string(),
      label: z.string(),
      type: z.enum(["text", "number", "currency", "percent", "points", "list"]),
    }),
  ),
  rows: z.array(
    z.object({
      id: z.string(),
      cells: z.record(
        z.string(),
        z.object({
          value: nullableText,
          detail: z.string().optional(),
          items: z.array(issue).optional(),
        }),
      ),
    }),
  ),
  total: z.number().int().nonnegative(),
  page: z.number().int().positive(),
  size: z.number().int().positive(),
});
export type Meta = z.infer<typeof metadata>;
export type Report = z.infer<typeof report>;
export type Overview = z.infer<typeof overview>;
export type Analysis = z.infer<typeof analysis>;
export type Metric = z.infer<typeof metric>;
export type Issue = z.infer<typeof issue>;
export type Action = z.infer<typeof action>;
