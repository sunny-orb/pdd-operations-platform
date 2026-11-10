import { z } from "zod";
import { ApiError as TransportError, getJson } from "../../shared/api/client";
import type { Mode, Scope } from "./state";
export class PromotionError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function get<T>(
  path: string,
  schema: z.ZodType<T>,
  params: URLSearchParams,
  signal?: AbortSignal,
) {
  let json: unknown;
  try {
    json = await getJson(`/api/v1/promotion/${path}?${params}`, signal);
  } catch (error) {
    if (error instanceof TransportError) throw new PromotionError(error.status, error.message);
    throw error;
  }
  return z
    .object({ data: schema, meta: z.object({ data_version: z.string(), request_id: z.string() }) })
    .parse(json);
}
export function params(mode: Mode, scope: Scope, version: string) {
  const result = new URLSearchParams({
    mode,
    date_from: scope.from,
    date_to: scope.to,
    data_version: version,
    page: String(scope.page),
    page_size: String(scope.size),
  });
  Object.entries({
    shop_id: scope.shop,
    channel: mode === "store" ? scope.channel : "",
    keyword: scope.keyword,
    category: scope.category,
    level: scope.level,
    operator: scope.operator,
  }).forEach(([key, value]) => {
    if (value) result.set(key, value);
  });
  scope.products.forEach((id) => result.append("listing_ids", id));
  return result;
}
