import { z } from "zod";

export type Scope = "overview" | "scores" | "days";
export type Scenario = "normal" | "empty" | "partial" | "error" | "pending" | "not-connected";
export type Mode = "preview" | "offline" | "api";
export type Filters = {
  dateFrom: string;
  dateTo: string;
  shop: string;
  operator: string;
  category: string;
  level: string;
  products: string[];
  keyword: string;
  judgment: string;
  page: number;
  pageSize: number;
};
export type MetricKey =
  "revenue" | "visitors" | "favorites" | "buyers" | "units" | "aov" | "conversion";
export type Product = {
  shopId: string;
  shopName: string;
  id: string;
  name: string;
  styleCode: string;
  category: string;
  level: string;
  operator: string;
  key: string;
};
export type Metric = {
  key: MetricKey;
  label: string;
  value: string | null;
  previous: string | null;
  yearAgo: string | null;
  mom: number | null;
  yoy: number | null;
  format: "money" | "number" | "percent";
};
export type Period = { days: number; value: string | null; change: number | null };
export type FunnelNode = { key: string; label: string; value: number | null; note: string };
export type Overview = {
  metrics: Metric[];
  periods: Period[];
  funnel: FunnelNode[];
  health: { title: string; description: string; tone: "warning" | "success" | "info" };
};
export type RankItem = {
  product?: Product;
  name: string;
  value: string | null;
  change: number | null;
  note: string;
};
export type Brief = {
  categories: RankItem[];
  products: RankItem[];
  declining: RankItem[];
  rising: RankItem[];
  actions: string[];
  window: { from: string; to: string };
};
export type ScoreMetric = {
  key: string;
  label: string;
  value: string | null;
  format: "money" | "number" | "percent" | "ratio";
  weight: number;
  tiers: number[];
  grade: string;
  points: number | null;
  reference: string;
  sampleSize: number;
  reason: string;
};
export type Score = {
  id: string;
  product: Product;
  date: string;
  modelVersion: string;
  stage: string;
  age: number;
  season: string;
  status: "complete" | "partial" | "unscored";
  score: number | null;
  verifiedScore: number;
  pendingWeight: number;
  recommendation: string;
  reason: string;
  metrics: ScoreMetric[];
};
export type Scores = {
  items: Score[];
  total: number;
  counts: { complete: number; partial: number; unscored: number };
};
export type Trend = {
  points: {
    date: string;
    current: number | null;
    lastYear: number | null;
    [key: string]: string | number | null;
  }[];
  series: { key: string; label: string; color: string }[];
  format: "money" | "number" | "percent";
  mode: "year" | "products" | "relative";
  note: string;
};
export type Diagnosis = {
  headline: string;
  evidence: string[];
  action: string;
  method: "rule" | "ai";
  generatedAt: string;
};
export type ProductAlert = {
  id: string;
  product: Product;
  priority: "P0" | "P1" | "P2";
  signal: string;
  current: number;
  baseline: number;
  change: number;
  trend: number[];
  revenue: string;
  impact: string;
  reasons: string[];
  action: string;
  dueAt: string | null;
  workflowState: string | null;
};
export type Alerts = { items: ProductAlert[]; total: number };
export type DayRow = {
  key: string;
  product: Product;
  date: string;
  visitors: number | null;
  revenue: string | null;
  conversion: number | null;
  yoy: number | null;
};
export type Days = { items: DayRow[]; total: number };
export type Options = {
  shops: { value: string; label: string }[];
  operators: string[];
  categories: string[];
  levels: string[];
  products: Product[];
  dateRange: { from: string; to: string };
};
export type Meta = {
  dataVersion: string;
  dataThrough: string | null;
  lastSuccessAt: string | null;
  completeness: "complete" | "partial" | "unknown";
  missingReasons: string[];
  isPreview: boolean;
};
export type Envelope<T> = {
  data: T | null;
  availability: "ready" | "empty" | "not_connected";
  computationState: "ready" | "pending" | "failed";
  meta: Meta;
};
export type RequestOptions = {
  mode: Mode;
  scenario: Scenario;
  metric?: MetricKey;
  comparison?: "year" | "products" | "relative";
  dataVersion?: string;
  versionToken?: number;
};

const decimal = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/)
  .nullable();
const num = z.number().finite().nullable();
const product = z.object({
  shopId: z.string(),
  shopName: z.string(),
  id: z.string(),
  name: z.string(),
  styleCode: z.string(),
  category: z.string(),
  level: z.string(),
  operator: z.string(),
  key: z.string(),
});
const metric = z.object({
  key: z.enum(["revenue", "visitors", "favorites", "buyers", "units", "aov", "conversion"]),
  label: z.string(),
  value: decimal,
  previous: decimal,
  yearAgo: decimal,
  mom: num,
  yoy: num,
  format: z.enum(["money", "number", "percent"]),
});
const scoreMetric = z.object({
  key: z.string(),
  label: z.string(),
  value: decimal,
  format: z.enum(["money", "number", "percent", "ratio"]),
  weight: z.number(),
  tiers: z.array(z.number()),
  grade: z.string(),
  points: num,
  reference: z.string(),
  sampleSize: z.number(),
  reason: z.string(),
});
const score = z.object({
  id: z.string(),
  product,
  date: z.string(),
  modelVersion: z.string(),
  stage: z.string(),
  age: z.number(),
  season: z.string(),
  status: z.enum(["complete", "partial", "unscored"]),
  score: num,
  verifiedScore: z.number(),
  pendingWeight: z.number(),
  recommendation: z.string(),
  reason: z.string(),
  metrics: z.array(scoreMetric),
});
const rank = z.object({
  product: product.optional(),
  name: z.string(),
  value: decimal,
  change: num,
  note: z.string(),
});
const schemas: Record<string, z.ZodType> = {
  filters: z.object({
    shops: z.array(z.object({ value: z.string(), label: z.string() })),
    operators: z.array(z.string()),
    categories: z.array(z.string()),
    levels: z.array(z.string()),
    products: z.array(product),
    dateRange: z.object({ from: z.string(), to: z.string() }),
  }),
  overview: z.object({
    metrics: z.array(metric),
    periods: z.array(z.object({ days: z.number(), value: decimal, change: num })),
    funnel: z.array(z.object({ key: z.string(), label: z.string(), value: num, note: z.string() })),
    health: z.object({
      title: z.string(),
      description: z.string(),
      tone: z.enum(["warning", "success", "info"]),
    }),
  }),
  brief: z.object({
    categories: z.array(rank),
    products: z.array(rank),
    declining: z.array(rank),
    rising: z.array(rank),
    actions: z.array(z.string()),
    window: z.object({ from: z.string(), to: z.string() }),
  }),
  scores: z.object({
    items: z.array(score),
    total: z.number(),
    counts: z.object({ complete: z.number(), partial: z.number(), unscored: z.number() }),
  }),
  score,
  trend: z
    .object({
      points: z.array(z.object({ date: z.iso.date(), current: num, lastYear: num }).catchall(num)),
      series: z.array(z.object({ key: z.string().min(1), label: z.string(), color: z.string() })),
      format: z.enum(["money", "number", "percent"]),
      mode: z.enum(["year", "products", "relative"]),
      note: z.string(),
    })
    .superRefine((trend, context) => {
      for (const series of trend.series) {
        trend.points.forEach((point, index) => {
          const value = point[series.key];
          if (value !== null && (typeof value !== "number" || !Number.isFinite(value))) {
            context.addIssue({
              code: "custom",
              path: ["points", index, series.key],
              message: "曲线序列缺失或不是数值",
            });
          }
        });
      }
    }),
  diagnosis: z.object({
    headline: z.string(),
    evidence: z.array(z.string()),
    action: z.string(),
    method: z.enum(["rule", "ai"]),
    generatedAt: z.string(),
  }),
  alerts: z.object({
    items: z.array(
      z.object({
        id: z.string(),
        product,
        priority: z.enum(["P0", "P1", "P2"]),
        signal: z.string(),
        current: z.number(),
        baseline: z.number(),
        change: z.number(),
        trend: z.array(z.number()),
        revenue: decimal,
        impact: decimal,
        reasons: z.array(z.string()),
        action: z.string(),
        dueAt: z.string().nullable(),
        workflowState: z.string().nullable(),
      }),
    ),
    total: z.number(),
  }),
  days: z.object({
    items: z.array(
      z.object({
        key: z.string(),
        product,
        date: z.string(),
        visitors: num,
        revenue: decimal,
        conversion: num,
        yoy: num,
      }),
    ),
    total: z.number(),
  }),
};
/** 校验接口形状与数值表示；缺数保留null，拒绝缺字段和非法经营值。 */
export function parseEnvelope<T>(endpoint: string, value: unknown): Envelope<T> {
  const schema = schemas[endpoint.startsWith("scores/") ? "score" : endpoint];
  if (!schema) throw new Error("未知商品接口");
  const result = z
    .object({
      data: schema.nullable(),
      availability: z.enum(["ready", "empty", "not_connected"]),
      computationState: z.enum(["ready", "pending", "failed"]),
      meta: z.object({
        dataVersion: z.string(),
        dataThrough: z.string().nullable(),
        lastSuccessAt: z.string().nullable(),
        completeness: z.enum(["complete", "partial", "unknown"]),
        missingReasons: z.array(z.string()),
        isPreview: z.boolean(),
      }),
    })
    .safeParse(value);
  if (
    !result.success ||
    (result.data.availability === "ready" &&
      result.data.computationState === "ready" &&
      result.data.data === null)
  )
    throw new Error("商品接口格式不符合约定，请核对字段与数据类型。");
  return result.data as Envelope<T>;
}
