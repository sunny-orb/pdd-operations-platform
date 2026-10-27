import { getJson } from "../../../shared/api/client";
import { filtersSchema, performanceSchema, shopsSchema, type Scope } from "./contracts";

export async function getSalesFilters(signal: AbortSignal) {
  return filtersSchema.parse(await getJson("/api/v1/sales/filters", signal));
}
/** 三个响应使用同一版本；本函数不计算经营指标，也不向来源提交任务。 */
export async function getSalesResult(scope: Scope, version: string, signal: AbortSignal) {
  const query = new URLSearchParams({
    date_from: scope.from,
    date_to: scope.to,
    data_version: version,
  });
  const selected = new URLSearchParams(query);
  if (scope.shop) selected.set("shop_id", scope.shop);
  const [performance, shops] = await Promise.all([
    getJson(`/api/v1/sales/performance?${selected}`, signal).then((value) =>
      performanceSchema.parse(value),
    ),
    getJson(`/api/v1/sales/shops?${query}`, signal).then((value) => shopsSchema.parse(value)),
  ]);
  if (performance.meta.data_version !== version || shops.meta.data_version !== version)
    throw new Error("数据版本不一致");
  return {
    missingShopDays: shops.data.missing_shop_days,
    performance: performance.data,
    shops: shops.data.items,
    meta: performance.meta,
  };
}
