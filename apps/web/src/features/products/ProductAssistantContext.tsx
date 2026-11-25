import { AssistantPageContext } from "../../shared/assistant/AssistantContext";
import { dimensionValues, type Scope } from "./scope";
import type { Catalog } from "./contracts";

/** 发布操作员已应用的筛选，目录只用于核实商品身份和显示名称。 */
export function ProductAssistantContext({
  scope,
  version,
  catalog,
  ready,
}: {
  scope: Scope;
  version: string;
  catalog?: Catalog;
  ready: boolean;
}) {
  const selected = scope.products;
  const selectedShops = dimensionValues(scope.shop);
  const selectedItem =
    ready && selected.length === 1
      ? catalog?.items.find(
          (item) =>
            item.value === selected[0] &&
            (!selectedShops.length || selectedShops.includes(item.shop)),
        )
      : undefined;
  return (
    <AssistantPageContext
      page="商品数据"
      selectionLabel={
        ready
          ? selectedShops
              .map((id) => catalog?.shops.find((shop) => shop.value === id)?.label ?? id)
              .join("、")
          : undefined
      }
      subject={
        selectedItem
          ? {
              listing_id: selectedItem.value,
              shop_id: selectedItem.shop,
              name: selectedItem.product_name ?? null,
              shop_name: catalog?.shops.find((s) => s.value === selectedItem.shop)?.label ?? null,
            }
          : null
      }
      context={
        ready
          ? {
              date_from: scope.from,
              date_to: scope.to,
              shop_id: selectedShops.length === 1 ? selectedShops[0] : undefined,
              shop_ids: selectedShops,
              product_version: version,
              domain: "products",
              listing_ids: selected,
              category_values: dimensionValues(scope.category),
              operator_values: dimensionValues(scope.operator),
              level_values: dimensionValues(scope.level),
              keyword: scope.keyword || undefined,
              category:
                typeof scope.category === "string" ? scope.category || undefined : undefined,
              operator:
                typeof scope.operator === "string" ? scope.operator || undefined : undefined,
              level: typeof scope.level === "string" ? scope.level || undefined : undefined,
            }
          : {}
      }
    />
  );
}
