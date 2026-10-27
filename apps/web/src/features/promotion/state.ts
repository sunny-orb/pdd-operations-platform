export type Mode = "store" | "product";
export type MetricKey =
  "spend" | "revenue" | "roi" | "impressions" | "clicks" | "ctr" | "cvr" | "cpc";
export type Scope = {
  from: string;
  to: string;
  shop: string;
  channel: string;
  keyword: string;
  category: string;
  level: string;
  operator: string;
  products: string[];
  page: number;
  size: number;
};
const keys = [
  "from",
  "to",
  "shop",
  "channel",
  "keyword",
  "category",
  "level",
  "operator",
  "page",
  "size",
] as const;
/** 未写入URL的日期始终使用当前版本推荐窗口；两个模式分别保存用户条件。 */
export function readScope(search: URLSearchParams, mode: Mode, from: string, to: string): Scope {
  const get = (key: string) => search.get(`${mode}_${key}`) ?? "";
  return {
    from: get("from") || from,
    to: get("to") || to,
    shop: get("shop"),
    channel: get("channel"),
    keyword: get("keyword"),
    category: get("category"),
    level: get("level"),
    operator: get("operator"),
    products: search.getAll(`${mode}_products`),
    page: Math.max(1, Number(get("page")) || 1),
    size:
      mode === "product" && [25, 50, 100].includes(Number(get("size"))) ? Number(get("size")) : 25,
  };
}
export function writeScope(search: URLSearchParams, mode: Mode, patch: Partial<Scope>) {
  const result = new URLSearchParams(search);
  keys.forEach((key) => {
    if (patch[key] !== undefined) {
      if (patch[key] === "") result.delete(`${mode}_${key}`);
      else result.set(`${mode}_${key}`, String(patch[key]));
    }
  });
  if (patch.products) {
    result.delete(`${mode}_products`);
    patch.products.forEach((id) => result.append(`${mode}_products`, id));
  }
  return result;
}
export function toggleMetric(selected: MetricKey[], key: MetricKey, mode: Mode): MetricKey[] {
  if (selected.includes(key))
    return selected.length === 1 ? selected : selected.filter((item) => item !== key);
  return mode === "store" ? [...selected.slice(-1), key] : [...selected, key];
}
export function dateError(scope: Pick<Scope, "from" | "to">) {
  if (!scope.from || !scope.to) return "请选择有效的开始和结束日期";
  if (
    ![scope.from, scope.to].every(
      (value) =>
        /^\d{4}-\d{2}-\d{2}$/.test(value) &&
        !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) &&
        new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value,
    )
  )
    return "请选择有效的日期";
  return scope.from > scope.to ? "结束日期不能早于开始日期" : null;
}
