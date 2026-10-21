import type { Filters, Options } from "../api/contracts";

/** 新选项到达后逐层剔除失效值；不凭旧选项清空所有下游筛选。 */
export function reconcileSelection(filters: Filters, options: Options): Partial<Filters> | null {
  if (filters.shop && !options.shops.some((s) => s.value === filters.shop)) return { shop: "" };
  if (filters.operator && !options.operators.includes(filters.operator)) return { operator: "" };
  if (filters.category && !options.categories.includes(filters.category)) return { category: "" };
  if (filters.level && !options.levels.includes(filters.level)) return { level: "" };
  const products = filters.products.filter((key) =>
    options.products.some((product) => product.key === key),
  );
  return products.length !== filters.products.length ? { products } : null;
}
