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
  validateDateRange(filters);
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
    scope,
    page: String(filters.page),
    page_size: String(filters.pageSize),
  });
  const fields = {
    date_from: filters.dateFrom,
    date_to: filters.dateTo || filters.dateFrom,
    shop_id: filters.shop,
    operator: filters.operator,
    category: filters.category,
    level: filters.level,
    keyword: filters.keyword,
    judgment: filters.judgment,
    data_version: options.dataVersion,
    metric: options.metric,
    comparison: options.comparison,
  };
  for (const [key, value] of Object.entries(fields)) if (value) params.set(key, value);
  for (const product of filters.products) params.append("products", product);
  const value = await getJson(`/api/v1/products/${endpoint}?${params}`, signal);
  if (signal?.aborted) throw abortError();
  return parseEnvelope<T>(endpoint, value);
}
