import { parseApiEnvelope } from "./wire";
import { validateDateRange } from "../state/filters";
import { getJson } from "../../../shared/api/client";
import {
  parseEnvelope,
  type Envelope,
  type Filters,
  type RequestOptions,
  type Scope,
} from "./contracts";

/** 明确未接入响应，不接触网络、不构造经营指标。 */
export function disconnectedEnvelope<T>(): Envelope<T> {
  return {
    data: null,
    availability: "not_connected",
    computationState: "ready",
    meta: {
      dataVersion: "not-connected",
      dataThrough: null,
      lastSuccessAt: null,
      completeness: "unknown",
      missingReasons: [],
      isPreview: false,
    },
  };
}
const abortError = () => new DOMException("请求已取消", "AbortError");
/** 同源商品查询适配；preview仅DEV动态加载，失败不会回退到样例。 */
export async function requestProducts<T>(
  endpoint: string,
  scope: Scope,
  filters: Filters,
  options: RequestOptions,
  signal?: AbortSignal,
): Promise<Envelope<T>> {
  if (signal?.aborted) throw abortError();
  if (scope !== "scores") validateDateRange(filters);
  if (options.mode === "offline") return disconnectedEnvelope<T>();
  if (options.mode === "preview") {
    if (!import.meta.env.DEV) throw new Error("生产环境不能启用开发样例。");
    const { getPreviewResponse } = await import("../preview/provider");
    const value = await getPreviewResponse(endpoint, scope, filters, options);
    if (signal?.aborted) throw abortError();
    return parseEnvelope<T>(endpoint, value);
  }
  if (
    !/^(filters|brief|overview|scores(?:\/[a-zA-Z0-9_-]+)?|trend|diagnosis|alerts|days)$/.test(
      endpoint,
    )
  )
    throw new Error("未知商品接口");
  const params = new URLSearchParams({
    ...(endpoint === "filters" || scope !== "scores"
      ? { scope: scope === "days" ? "detail" : scope }
      : {}),
    page: String(endpoint === "filters" ? 1 : filters.page),
    page_size: String(endpoint === "filters" ? 100 : filters.pageSize),
  });
  const fields = {
    date_from: scope !== "scores" ? filters.dateFrom : undefined,
    date_to: scope !== "scores" ? filters.dateTo || filters.dateFrom : undefined,
    shop_id: filters.shop,
    operator: filters.operator,
    category: filters.category,
    level: scope !== "scores" ? filters.level : undefined,
    keyword: filters.keyword,
    decision: scope === "scores" ? filters.judgment : undefined,
    score_version: scope === "scores" ? options.scoreVersion : undefined,
    data_version: options.dataVersion,
    metric: options.metric
      ? ((
          {
            revenue: "product_revenue",
            aov: "revenue_per_buyer",
            conversion: "buyer_conversion_rate",
          } as Record<string, string>
        )[options.metric] ?? options.metric)
      : undefined,
    comparison: options.comparison,
  };
  for (const [key, value] of Object.entries(fields)) if (value) params.set(key, value);
  if (filters.products.length > 20) throw new Error("一次最多对比20款商品。");
  if (endpoint !== "filters")
    for (const product of filters.products) {
      const parts: unknown = JSON.parse(product);
      if (
        !Array.isArray(parts) ||
        parts.length !== 2 ||
        !parts.every((p) => typeof p === "string" && p && !p.includes(":"))
      )
        throw new Error("商品身份格式错误，请重新选择。");
      params.append("product_key", `pdd:${parts[0]}:${parts[1]}`);
    }
  const value = await getJson(`/api/v1/products/${endpoint}?${params}`, signal);
  if (signal?.aborted) throw abortError();
  return parseApiEnvelope<T>(endpoint, value);
}
