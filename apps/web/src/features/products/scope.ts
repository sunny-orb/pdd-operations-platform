export type Scope = {
  from: string;
  to: string;
  shop: string;
  operator: string;
  category: string;
  level: string;
  keyword: string;
  products: string[];
  page: number;
  size: number;
  judgement: string;
};
export type ScopeName = "main" | "score" | "detail";
export function readScope(p: URLSearchParams, name: ScopeName, from: string, to: string): Scope {
  const get = (key: string) => p.get(`${name}_${key}`) ?? "";
  const start = get("from") || get("to") || from,
    end = get("to") || get("from") || to;
  return {
    from: start,
    to: end,
    shop: get("shop"),
    operator: get("operator"),
    category: get("category"),
    level: get("level"),
    keyword: get("keyword"),
    products: p.getAll(`${name}_listing`),
    page: Math.max(1, Number(get("page")) || 1),
    size:
      name === "score"
        ? 12
        : [25, 50, 100].includes(Number(get("size")))
          ? Number(get("size"))
          : 25,
    judgement: get("judgement"),
  };
}
export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}
export function scopeError(scope: Scope, min?: string, max?: string): string | null {
  if (!validDate(scope.from) || !validDate(scope.to)) return "请输入有效日期（YYYY-MM-DD）。";
  if (scope.to < scope.from) return "结束日期不能早于开始日期，请修改后再查询。";
  if ((min && scope.from < min) || (max && scope.to > max))
    return "日期超出当前已接收范围，请修改后再查询。";
  return null;
}
export function writeScope(params: URLSearchParams, name: ScopeName, scope: Scope) {
  const next = new URLSearchParams(params);
  [...next.keys()].filter((k) => k.startsWith(`${name}_`)).forEach((k) => next.delete(k));
  for (const [key, value] of Object.entries(scope)) {
    if (key === "products") scope.products.forEach((id) => next.append(`${name}_listing`, id));
    else if (value !== "" && value != null) next.set(`${name}_${key}`, String(value));
  }
  return next;
}
