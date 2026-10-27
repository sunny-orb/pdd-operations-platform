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
  const start = get("from") || from,
    end = get("to") || to;
  return {
    from: start <= end ? start : end,
    to: start <= end ? end : start,
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
export function writeScope(params: URLSearchParams, name: ScopeName, scope: Scope) {
  const next = new URLSearchParams(params);
  [...next.keys()].filter((k) => k.startsWith(`${name}_`)).forEach((k) => next.delete(k));
  for (const [key, value] of Object.entries(scope)) {
    if (key === "products") scope.products.forEach((id) => next.append(`${name}_listing`, id));
    else if (value !== "" && value != null) next.set(`${name}_${key}`, String(value));
  }
  return next;
}
