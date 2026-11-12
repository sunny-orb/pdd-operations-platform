export type Dimension = string | string[];

export function dimensionValues(value: Dimension) {
  return Array.isArray(value) ? value : value ? [value] : [];
}

export function dimensionSelection(values: string[]): Dimension {
  const unique = [...new Set(values.filter(Boolean))];
  return unique.length > 1 ? unique : (unique[0] ?? "");
}

export type Scope = {
  from: string;
  to: string;
  shop: Dimension;
  operator: Dimension;
  category: Dimension;
  level: Dimension;
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
    shop: dimensionSelection(p.getAll(`${name}_shop`)),
    operator: dimensionSelection(p.getAll(`${name}_operator`)),
    category: dimensionSelection(p.getAll(`${name}_category`)),
    level: dimensionSelection(p.getAll(`${name}_level`)),
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
    else if (Array.isArray(value)) value.forEach((item) => next.append(`${name}_${key}`, item));
    else if (value !== "" && value != null) next.set(`${name}_${key}`, String(value));
  }
  return next;
}

/** 仅日期输入会固定日期；普通筛选后默认窗口仍跟随新版本。 */
export function patchScope(
  params: URLSearchParams,
  name: ScopeName,
  patch: Partial<Scope>,
  from: string,
  to: string,
) {
  const explicit =
    params.has(`${name}_from`) ||
    params.has(`${name}_to`) ||
    patch.from !== undefined ||
    patch.to !== undefined;
  const next = writeScope(params, name, { ...readScope(params, name, from, to), ...patch });
  if (!explicit) {
    next.delete(`${name}_from`);
    next.delete(`${name}_to`);
  }
  return next;
}
