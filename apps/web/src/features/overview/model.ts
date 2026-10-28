import { formatFixedDecimal } from "../../shared/format/decimal";
export type DomainId = "sales" | "products" | "promotion" | "aftersales";
export type ValueType = "number" | "currency" | "percent" | "ratio";
export type Field = {
  label: string;
  value: string | null;
  type: ValueType | "text";
  note?: string;
};
export type Observation = {
  id: string;
  domain: DomainId;
  level: "observation";
  sourcePriority: string | null;
  title: string;
  detail: string;
  scope: string;
  impact: string | null;
  impactKind: string;
  link: string;
};
export type SuggestedAction = {
  id: string;
  issue: string;
  impact: string | null;
  impactKind: string;
  hypothesis: string;
  suggestion: string;
  assignee: "未分配";
  due: "未登记";
  progress: "建议，未登记";
  verification: string;
  link: string;
};
export type DomainCard = {
  title: string;
  heading: string;
  value: string | null;
  valueType: ValueType;
  valueLabel: string;
  pairs: Field[];
  note: string;
  link: string;
  status: string;
};
export type DomainPayload = {
  card: DomainCard;
  observations: Observation[];
  actions: SuggestedAction[];
};
export type DomainMeta<M = unknown> = {
  id: DomainId;
  version: string;
  /** 同一发布版下仍会改变查询的经营范围，如默认月和日历上限。 */
  scopeKey?: string;
  date: string | null;
  completeThrough: string | null;
  success: string | null;
  attempt: string | null;
  attemptStatus: string | null;
  status: string;
  notes: string[];
  raw: M;
};
export const domains = [
  { id: "sales", label: "店铺 GMV", link: "/sales" },
  { id: "products", label: "商品经营", link: "/products" },
  { id: "promotion", label: "推广效率", link: "/promotion?mode=product" },
  { id: "aftersales", label: "售后退款", link: "/aftersales" },
] as const;
/** 只格式化API十进制原值，不合计金额、比率或增长；原型金额显示到元。 */
export function formatMetric(
  value: string | null | undefined,
  type: ValueType = "number",
  digits?: number,
) {
  const text = formatFixedDecimal(
    value,
    digits ?? (type === "currency" || type === "number" ? 0 : 2),
    type === "percent" ? 2 : 0,
    type !== "percent" && type !== "ratio",
  );
  if (text === null) return "—";
  return type === "currency" ? `¥${text}` : type === "percent" ? `${text}%` : text;
}
export function localTime(value: string | null | undefined) {
  if (!value) return "尚无记录";
  return Number.isNaN(Date.parse(value))
    ? value
    : new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}
/** M6-01尚未裁决；列表/建议长度仅是当前呈现结构数，不发布正式异常总数。 */
export function centerCounts(observations: readonly unknown[], actions: readonly unknown[]) {
  return {
    exceptionCount: null,
    p0: null,
    p1: null,
    p2: null,
    assignees: null,
    observations: observations.length,
    actionRows: actions.length,
  };
}
export function shownAlerts<T extends { level: string }>(items: readonly T[], level: string): T[] {
  return level === "全部" ? [...items] : items.filter((item) => item.level === level);
}
export function productLink(date: string, object?: { listing_id: string; shop_id: string }) {
  const q = new URLSearchParams({ main_from: date, main_to: date });
  if (object) {
    q.set("main_shop", object.shop_id);
    q.set("main_listing", object.listing_id);
  }
  return `/products?${q}#risk`;
}
export function promotionLink(
  date: string,
  object?: { listing_id: string | null; shop_id: string | null },
) {
  const q = new URLSearchParams({ mode: "product", product_from: date, product_to: date });
  if (object?.shop_id) q.set("product_shop", object.shop_id);
  if (object?.listing_id) q.set("product_products", object.listing_id);
  return `/promotion?${q}#promotion-diagnosis`;
}
export function aftersalesLink(reportId: string, category?: string) {
  const q = new URLSearchParams({ report_id: reportId });
  if (category) q.set("category", category);
  return `/aftersales?${q}#aftersales-analysis`;
}
export function actionFromObservation(
  item: Observation,
  suggestion: string,
  hypothesis: string,
  verification: string,
): SuggestedAction {
  return {
    id: item.id,
    issue: item.title,
    impact: item.impact,
    impactKind: item.impactKind,
    hypothesis,
    suggestion,
    assignee: "未分配",
    due: "未登记",
    progress: "建议，未登记",
    verification,
    link: item.link,
  };
}
export function safeLink(value: string | null | undefined) {
  return value && /^https:\/\//i.test(value) ? value : undefined;
}
