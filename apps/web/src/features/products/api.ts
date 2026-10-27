import { z } from "zod";
import type { Scope } from "./scope";
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(code);
  }
}
export async function get<T>(
  path: string,
  schema: z.ZodType<T>,
  params: URLSearchParams,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/v1/products/${path}?${params}`, { signal });
  const json: unknown = await response.json();
  if (!response.ok) {
    const error = z.object({ error: z.object({ code: z.string() }) }).safeParse(json);
    throw new ApiError(response.status, error.success ? error.data.error.code : "REQUEST_FAILED");
  }
  return z
    .object({ data: schema, meta: z.object({ data_version: z.string(), request_id: z.string() }) })
    .parse(json).data;
}
export function params(scope: Scope, version: string) {
  const p = new URLSearchParams({
    date_from: scope.from,
    date_to: scope.to,
    data_version: version,
    page: String(scope.page),
    page_size: String(scope.size),
  });
  for (const [key, value] of Object.entries({
    shop_id: scope.shop,
    operator: scope.operator,
    category: scope.category,
    level: scope.level,
    keyword: scope.keyword,
    judgement: scope.judgement,
  }))
    if (value) p.set(key, value);
  scope.products.forEach((id) => p.append("listing_ids", id));
  return p;
}
