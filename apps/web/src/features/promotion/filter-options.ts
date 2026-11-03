import type { z } from "zod";
import type { options } from "./contracts";
import type { Scope } from "./state";

export type FilterOptions = z.infer<typeof options>;
export type ProductOption = FilterOptions["products"][number];

function searchMatches(row: ProductOption, text: string) {
  const needle = text.trim().toLocaleLowerCase();
  return (
    !needle ||
    [row.product_id, row.product_name ?? "", ...(row.search_names ?? [])]
      .join(" ")
      .toLocaleLowerCase()
      .includes(needle)
  );
}

/** 选项来自完整日期目录；只有商品搜索草稿缩小商品候选，经营条件互不改写。 */
export function filterOptions(
  catalog: FilterOptions,
  scope: Scope,
  needle: string,
  known: ProductOption[] = [],
): FilterOptions {
  const rows = catalog.products;
  const values = (key: "category" | "level" | "operator") =>
    [
      ...new Set(rows.map((row) => row[key]).filter((value): value is string => Boolean(value))),
    ].sort();
  const candidates = rows.filter((row) => searchMatches(row, needle));
  const identities = new Map(
    [...known, ...catalog.selected_products, ...rows].map((row) => [row.value, row]),
  );
  return {
    ...catalog,
    shops: catalog.shops,
    products: candidates,
    selected_products: scope.products
      .map((id) => identities.get(id))
      .filter((row): row is ProductOption => !!row),
    categories: values("category"),
    levels: values("level"),
    operators: values("operator"),
    total: candidates.length,
  };
}
