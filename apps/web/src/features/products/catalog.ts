import type { Catalog, Options } from "./contracts";
import type { Scope } from "./scope";
import { matchesProductLevel, productLevelGroup } from "../../shared/products/levels";

/** 只筛轻量目录；顺序沿用店铺→运营→品类→等级，搜索不清除有效已选商品。 */
export function catalogOptions(catalog: Catalog, scope: Scope, needle = ""): Options {
  const shops = catalog.items.filter((item) => !scope.shop || item.shop === scope.shop);
  const operator = shops.some((item) => item.operator === scope.operator) ? scope.operator : "";
  const operators = shops.filter((item) => !operator || item.operator === operator);
  const category = operators.some((item) => item.category === scope.category) ? scope.category : "";
  const categories = operators.filter((item) => !category || item.category === category);
  const level = categories.some((item) => matchesProductLevel(item, scope.level))
    ? scope.level
    : "";
  const candidates = categories.filter((item) => matchesProductLevel(item, level));
  const levels = new Set(categories.map(productLevelGroup));
  if (level) levels.add(level);
  const ids = new Set(candidates.map((item) => item.value));
  const search = (needle || scope.keyword).toLowerCase();
  const products = candidates.filter((item) => item.search_text.toLowerCase().includes(search));
  const values = (items: Catalog["items"], field: "operator" | "category" | "level") =>
    [
      ...new Set(items.map((item) => item[field]).filter((value): value is string => !!value)),
    ].sort();
  return {
    shops: catalog.shops,
    operators: values(shops, "operator"),
    categories: values(operators, "category"),
    levels: [...levels].sort(),
    products,
    total: products.length,
    selection: { operator, category, level, products: scope.products.filter((id) => ids.has(id)) },
    selected_products: catalog.items.filter((item) => scope.products.includes(item.value)),
  };
}
