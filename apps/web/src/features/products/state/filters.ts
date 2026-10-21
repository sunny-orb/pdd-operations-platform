import type { Filters, Scope } from "../api/contracts";

/** 只用于样例的日期由开发适配提供；真实未接入默认不假定最新日期。 */
export function buildDefaultFilters(): Filters {
  return {
    dateFrom: "",
    dateTo: "",
    shop: "",
    operator: "",
    category: "",
    level: "",
    products: [],
    keyword: "",
    judgment: "",
    page: 1,
    pageSize: 25,
  };
}
export const productKey = (shopId: string, productId: string) =>
  JSON.stringify([shopId, productId]);
const fields = [
  "dateFrom",
  "dateTo",
  "shop",
  "operator",
  "category",
  "level",
  "keyword",
  "judgment",
] as const;
const validDate = (value: string) => {
  if (!value) return true;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
};
/** 在请求前拒绝无效自然日或反向日期；不清空、不交换用户输入。 */
export function validateDateRange(filters: Filters): void {
  if (!validDate(filters.dateFrom) || !validDate(filters.dateTo))
    throw new Error("日期无效，请输入真实自然日。");
  if (filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo)
    throw new Error("日期范围无效：结束日期不能早于开始日期。");
}
/** 范围内更新：查询条件变更重置页码，显式翻页保留目标页。 */
export function updateFilters(current: Filters, patch: Partial<Filters>): Filters {
  const changed = Object.keys(patch).some(
    (key) =>
      key !== "page" &&
      key !== "pageSize" &&
      JSON.stringify(current[key as keyof Filters]) !== JSON.stringify(patch[key as keyof Filters]),
  );
  return {
    ...current,
    ...patch,
    page:
      changed || (patch.pageSize !== undefined && patch.pageSize !== current.pageSize)
        ? 1
        : (patch.page ?? current.page),
  };
}
/** 三个URL命名空间互不覆盖，忽略非法商品键与越界分页。 */
export function readFilters(params: URLSearchParams, scope: Scope): Filters {
  const result = buildDefaultFilters();
  for (const key of fields) result[key] = params.get(`${scope}.${key}`) ?? "";
  const page = Number(params.get(`${scope}.page`) ?? 1);
  const size = Number(params.get(`${scope}.pageSize`) ?? 25);
  result.page = Number.isInteger(page) && page > 0 ? page : 1;
  result.pageSize = Number.isInteger(size) && size > 0 ? Math.min(size, 100) : 25;
  result.products = params.getAll(`${scope}.products`).filter((key) => {
    try {
      const v: unknown = JSON.parse(key);
      return Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "string");
    } catch {
      return false;
    }
  });
  return result;
}
export function writeFilters(
  params: URLSearchParams,
  scope: Scope,
  filters: Filters,
): URLSearchParams {
  const result = new URLSearchParams(params);
  for (const key of [...result.keys()]) if (key.startsWith(`${scope}.`)) result.delete(key);
  for (const key of fields)
    if (filters[key] || key === "dateFrom" || key === "dateTo")
      result.set(`${scope}.${key}`, filters[key]);
  for (const value of filters.products) result.append(`${scope}.products`, value);
  if (filters.page !== 1) result.set(`${scope}.page`, String(filters.page));
  if (filters.pageSize !== 25) result.set(`${scope}.pageSize`, String(filters.pageSize));
  return result;
}
