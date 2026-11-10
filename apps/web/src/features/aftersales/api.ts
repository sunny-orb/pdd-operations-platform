import { z } from "zod";
import { ApiError as TransportError, getJson } from "../../shared/api/client";
import { metadata } from "./contracts";
import type { Scope } from "./state";
export class AftersalesError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
/** 请求仅访问本地只读业务API；版本冲突由页面整组重取。 */
export async function get<T>(
  path: string,
  schema: z.ZodType<T>,
  query: URLSearchParams,
  signal?: AbortSignal,
) {
  let json: unknown;
  try {
    json = await getJson(`/api/v1/aftersales/${path}?${query}`, signal);
  } catch (error) {
    if (error instanceof TransportError) throw new AftersalesError(error.status, error.message);
    throw error;
  }
  return z.object({ data: schema, meta: metadata }).parse(json);
}
export function params(scope: Scope, version: string | null) {
  const result = new URLSearchParams({
    focus: scope.focus,
    view: scope.view,
    page: String(scope.page),
    size: String(scope.size),
  });
  Object.entries({
    report_id: scope.reportExplicit ? scope.report : null,
    category: scope.category,
    keyword: scope.keyword,
    data_version: version,
  }).forEach(([key, value]) => {
    if (value) result.set(key, value);
  });
  return result;
}
