import { formatFixedDecimal } from "../../shared/format/decimal";
import type { UpdateSource } from "./updateStatus";
export type DomainId = "sales" | "products" | "promotion" | "aftersales";
export type ValueType = "number" | "currency" | "percent" | "ratio";
export type Field = {
  label: string;
  value: string | null;
  type: ValueType | "text";
  note?: string;
};
export type Observation = {
  /** 只保留负责域返回的规则及版本，中心不重新判断或分级。 */
  sourceRules?: string[];
  ruleVersion?: string;
  id: string;
  domain: DomainId;
  level: "observation";
  sourcePriority: string | null;
  objectKey: string | null;
  objectLevel: "listing" | "overview" | "category" | "style" | "unknown";
  title: string;
  detail: string;
  scope: string;
  impact: string | null;
  impactKind: string;
  link: string;
};
export type SuggestedAction = {
  id: string;
  /** 仅商品/推广提供归并身份；售后沿原始项透传。 */
  domain?: DomainId;
  objectKey?: string | null;
  issue: string;
  impact: string | null;
  impactKind: string;
  hypothesis: string;
  suggestion: string;
  assignee: "未分配" | "待分配";
  due: "未登记" | "待分配";
  progress: "建议，未登记" | "待核查";
  reviewDate?: "待分配";
  context?: Pick<
    Observation,
    "scope" | "detail" | "sourcePriority" | "sourceRules" | "ruleVersion"
  >;
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
  sourceNote?: string;
  link: string;
  status: string;
};
export type DomainPayload = {
  card: DomainCard;
  observations: Observation[];
  actions: SuggestedAction[];
  observationState: "observed" | "pending_validation" | "not_available" | "not_implemented";
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
  updates?: UpdateSource[];
  raw: M;
};
export const domains = [
  { id: "sales", label: "店铺 GMV", link: "/sales" },
  { id: "products", label: "商品数据", link: "/products" },
  { id: "promotion", label: "推广效率", link: "/promotion?mode=product" },
  { id: "aftersales", label: "售后退款", link: "/aftersales" },
] as const;
export function sourceLabel(domain: DomainId | undefined) {
  return domains.find((item) => item.id === domain)?.label ?? "来源";
}
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
export type ObservationCoverage = {
  domain: DomainId;
  status:
    | "observed"
    | "retained"
    | "failed"
    | "loading"
    | "not_connected"
    | "not_implemented"
    | "pending_validation";
};
/** 观察列表的读取覆盖与业务完整日分开；失败保旧明确标为retained。 */
export function observationCoverage(
  states: readonly {
    id: DomainId;
    data: DomainPayload | undefined;
    error: string | null;
    loading: boolean;
    observationLoading?: boolean;
    observationError?: string | null;
    retained?: boolean;
  }[],
): ObservationCoverage[] {
  return states.map((state) => ({
    domain: state.id,
    status:
      state.data?.observationState === "observed"
        ? state.error || state.retained
          ? "retained"
          : "observed"
        : state.loading || state.observationLoading
          ? "loading"
          : state.error || state.observationError
            ? "failed"
            : state.data?.observationState === "pending_validation"
              ? "pending_validation"
              : state.data?.observationState === "not_implemented"
                ? "not_implemented"
                : "not_connected",
  }));
}
/** M6-01：只计已映射观察对象；标题不参与身份，正式分级与负责人继续未知。 */
export function centerCounts(
  observations: readonly Observation[],
  actions: readonly unknown[],
  coverage: readonly ObservationCoverage[],
) {
  const mapped = observations.filter((o) => o.objectKey && o.objectLevel !== "unknown");
  const objects = new Set(mapped.map((o) => o.objectKey));
  const unmapped = observations.length - mapped.length;
  const available = coverage.filter(
    (c) => c.status === "observed" || c.status === "retained",
  ).length;
  return {
    exceptionCount: objects.size || (available && !unmapped ? 0 : null),
    countStatus: unmapped ? "partial_mapping" : available ? "observed" : "unavailable",
    unmappedObservations: unmapped,
    availableDomains: available,
    unavailableDomains: coverage.length - available,
    p0: null,
    p1: null,
    p2: null,
    assignees: null,
    observations: observations.length,
    actionRows: actions.length,
  };
}

export type SourcePriority = "P0" | "P1" | "P2";
export type AlertObject = {
  id: string;
  objectKey: string | null;
  sourcePriority: SourcePriority | null;
  members: Observation[];
};
export type ActionObject = {
  id: string;
  objectKey: string | null;
  members: SuggestedAction[];
  suggestions: string[];
};
const sourcePriorityOrder = ["P0", "P1", "P2"] as const;
const isListingDomain = (domain: DomainId | undefined) =>
  domain === "products" || domain === "promotion";
const priorityRank = (priority: SourcePriority | null) =>
  priority === null ? 3 : sourcePriorityOrder.indexOf(priority);

/** 中心展示投影：只归并完整商品/推广身份，保留来源证据和金额；售后逐项透传。 */
export function buildCenterView(
  observations: readonly Observation[],
  actions: readonly SuggestedAction[],
  coverage: readonly ObservationCoverage[],
) {
  const alertMap = new Map<string, AlertObject>();
  observations.forEach((item, index) => {
    const key =
      isListingDomain(item.domain) && item.objectLevel === "listing" && item.objectKey
        ? `object:${item.objectKey}`
        : `observation:${item.domain}:${item.id}:${index}`;
    const group = alertMap.get(key) ?? {
      id: key,
      objectKey: item.objectKey,
      sourcePriority: null,
      members: [],
    };
    group.members.push(item);
    // 原域等级仅用于商品/推广处理顺序，绝不覆盖正式中心等级或售后状态。
    if (isListingDomain(item.domain)) {
      group.sourcePriority =
        sourcePriorityOrder.find((priority) =>
          group.members.some((member) => member.sourcePriority === priority),
        ) ?? null;
    }
    alertMap.set(key, group);
  });
  const alerts = [...alertMap.values()].sort(
    (a, b) => priorityRank(a.sourcePriority) - priorityRank(b.sourcePriority),
  );
  const actionMap = new Map<string, ActionObject>();
  actions.forEach((item, index) => {
    if (isListingDomain(item.domain) && !item.suggestion.trim()) return;
    const key =
      isListingDomain(item.domain) && item.objectKey
        ? `object:${item.objectKey}`
        : `action:${item.id}:${index}`;
    const group = actionMap.get(key) ?? {
      id: key,
      objectKey: item.objectKey ?? null,
      members: [],
      suggestions: [],
    };
    group.members.push(item);
    const suggestion = isListingDomain(item.domain) ? item.suggestion.trim() : item.suggestion;
    if (!group.suggestions.includes(suggestion)) group.suggestions.push(suggestion);
    actionMap.set(key, group);
  });
  const groupedActions = [...actionMap.values()].sort(
    (a, b) =>
      priorityRank(alertMap.get(a.id)?.sourcePriority ?? null) -
      priorityRank(alertMap.get(b.id)?.sourcePriority ?? null),
  );
  const sourceAlerts = alerts.filter((group) => isListingDomain(group.members[0].domain));
  const mapped = sourceAlerts.filter(
    (group) => group.objectKey && group.members[0].objectLevel === "listing",
  );
  const sourceAvailable = coverage.some(
    (c) => isListingDomain(c.domain) && (c.status === "observed" || c.status === "retained"),
  );
  const sourceCounts =
    mapped.length || (sourceAvailable && !sourceAlerts.length)
      ? {
          P0: mapped.filter((x) => x.sourcePriority === "P0").length,
          P1: mapped.filter((x) => x.sourcePriority === "P1").length,
          P2: mapped.filter((x) => x.sourcePriority === "P2").length,
          ungraded: mapped.filter((x) => x.sourcePriority === null).length,
        }
      : null;
  const counts = centerCounts(observations, groupedActions, coverage);
  return {
    alerts,
    actions: groupedActions,
    counts,
    sourceCounts,
    actionCount: groupedActions.length || (counts.availableDomains ? 0 : null),
    sourceActionRows: actions.length,
    unmappedActions: groupedActions.filter(
      (group) => isListingDomain(group.members[0].domain) && !group.objectKey,
    ).length,
  };
}
const hasIdentity = (value: string | null | undefined): value is string =>
  typeof value === "string" && value.trim().length > 0;
/** 完整店铺+链接在M1/M2共用身份命名空间，不将同品类或同商品裸ID合并。 */
export function listingObjectKey(object: { shop_id: string | null; listing_id: string | null }) {
  return hasIdentity(object.shop_id) && hasIdentity(object.listing_id)
    ? JSON.stringify(["listing", object.shop_id, object.listing_id])
    : null;
}
/** 报告与层级都参与身份；总览和所属品类分别计项，不据此合计金额。 */
export function aftersalesObjectKey(
  report: string,
  level: "overview" | "category" | "style",
  object: string | null,
) {
  return hasIdentity(report) && hasIdentity(object)
    ? JSON.stringify(["aftersales", report, level, object])
    : null;
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
/** 风险定位保留用于排名的来源期间；默认全店不追加店铺过滤，链接ID已含店铺身份。 */
export function productRiskLink(period: { start: string; end: string }, listingId?: string) {
  const q = new URLSearchParams({ main_from: period.start, main_to: period.end });
  if (listingId) q.set("main_listing", listingId);
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
export function salesLink(month: string, date: string | null) {
  const q = new URLSearchParams({ month });
  if (date) {
    q.set("from", date);
    q.set("to", date);
  }
  return `/sales?${q}#performance`;
}
export function actionFromObservation(
  item: Observation,
  suggestion: string,
  hypothesis: string,
  verification: string,
): SuggestedAction {
  return {
    id: item.id,
    domain: item.domain,
    objectKey: item.objectLevel === "listing" ? item.objectKey : null,
    issue: item.title,
    impact: item.impact,
    impactKind: item.impactKind,
    hypothesis,
    suggestion,
    assignee: item.domain === "aftersales" ? "未分配" : "待分配",
    due: item.domain === "aftersales" ? "未登记" : "待分配",
    progress: item.domain === "aftersales" ? "建议，未登记" : "待核查",
    ...(item.domain === "aftersales"
      ? {}
      : {
          reviewDate: "待分配" as const,
          context: {
            scope: item.scope,
            detail: item.detail,
            sourcePriority: item.sourcePriority,
            sourceRules: item.sourceRules,
            ruleVersion: item.ruleVersion,
          },
        }),
    verification,
    link: item.link,
  };
}
export function safeLink(value: string | null | undefined) {
  return value && /^https:\/\//i.test(value) ? value : undefined;
}
