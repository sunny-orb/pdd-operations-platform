import { sameProduct, type AssistantScope, type PageContext } from "./contracts";

export const globalScope: AssistantScope = {
  page: "运营看板中心",
  subject: null,
  context: { domain: "all" },
};

/** 只比较用户能调整的筛选，后台更新版本不会打断对话。 */
function pageFilterKey(context: PageContext) {
  const fields: (keyof PageContext)[] = [
    "domain",
    "date_from",
    "date_to",
    "month",
    "report_id",
    "category",
    "operator",
    "level",
    "keyword",
    "product_key",
    "mode",
    "view",
    "focus",
    "shop_id",
    "shop_ids",
    "listing_ids",
    "category_values",
    "operator_values",
    "level_values",
  ];
  return JSON.stringify(
    fields.map((key) => {
      const value = context[key];
      return Array.isArray(value) ? [...value].sort().join("\u0000") : (value ?? "");
    }),
  );
}

export function samePageFilters(a: AssistantScope, b: AssistantScope) {
  return (
    a.page === b.page &&
    sameProduct(a.subject, b.subject) &&
    pageFilterKey(a.context) === pageFilterKey(b.context)
  );
}

/** 显示已应用的筛选；展示名称仅留在界面，查询继续使用业务 ID。 */
export function scopeSummary({ page, subject, context: c, selectionLabel }: AssistantScope) {
  const dates =
    c.date_from && c.date_to
      ? c.date_from === c.date_to
        ? c.date_from
        : c.date_from + " 至 " + c.date_to
      : c.month;
  const parts = [
    subject?.name ?? (c.listing_ids?.length ? `${c.listing_ids.length} 款商品` : undefined),
    selectionLabel ||
      subject?.shop_name ||
      (c.shop_ids?.length ? `${c.shop_ids.length} 家店铺` : undefined),
    ...(c.category_values ?? [c.category]),
    ...(c.operator_values ?? [c.operator]),
    ...(c.level_values ?? [c.level]),
    c.keyword,
    c.focus === "size" ? "尺码问题" : c.focus === "quality" ? "质量问题" : undefined,
  ].filter(Boolean);
  if (!parts.length) parts.push(page);
  if (dates) parts.push(dates);
  return parts.join(" · ");
}
