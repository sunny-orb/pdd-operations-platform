import type { Catalog, Options } from "./contracts";
import { dimensionSelection, dimensionValues, type Scope } from "./scope";
import { matchesProductLevel, productLevelGroup } from "../../shared/products/levels";

/** 只筛轻量目录；顺序沿用店铺→运营→品类→等级，搜索不清除有效已选商品。 */
export function catalogOptions(catalog: Catalog, scope: Scope, needle = ""): Options {
  const selectedShops = dimensionValues(scope.shop);
  const shops = catalog.items.filter(
    (item) => !selectedShops.length || selectedShops.includes(item.shop),
  );
  const operator = dimensionValues(scope.operator).filter((value) =>
    shops.some((item) => item.operator === value),
  );
  const operators = shops.filter(
    (item) => !operator.length || operator.some((value) => value === item.operator),
  );
  const category = dimensionValues(scope.category).filter((value) =>
    operators.some((item) => item.category === value),
  );
  const categories = operators.filter(
    (item) => !category.length || category.some((value) => value === item.category),
  );
  const level = dimensionValues(scope.level).filter((value) =>
    categories.some((item) => matchesProductLevel(item, value)),
  );
  const candidates = categories.filter(
    (item) => !level.length || level.some((value) => matchesProductLevel(item, value)),
  );
  const levels = new Set([...categories.map(productLevelGroup), ...level]);
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
    selection: {
      operator: dimensionSelection(operator),
      category: dimensionSelection(category),
      level: dimensionSelection(level),
      products: scope.products.filter((id) => ids.has(id)),
    },
    selected_products: catalog.items.filter((item) => scope.products.includes(item.value)),
  };
}
