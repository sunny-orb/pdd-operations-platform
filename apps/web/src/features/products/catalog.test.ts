import { expect, it } from "vitest";
import { catalogOptions } from "./catalog";
import { readScope } from "./scope";

const scope = readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-31");
const catalog = {
  shops: [{ value: "s1", label: "甲店" }],
  total: 4,
  items: ["A级", "A级/B级混合", "B级/清仓混合", null].map((level, i) => ({
    value: String(i),
    label: `商品${i}`,
    search_text: `商品${i}`,
    shop: "s1",
    operator: "甲",
    category: "帽子",
    level,
    level_group: ["A级", "多等级", "多等级", "未匹配"][i],
    level_details: [["A级"], ["A级", "B级"], ["B级", "清仓"], []][i],
  })),
};

it("多等级合并各组合且不重复，基础等级和未匹配独立筛选", () => {
  expect(catalogOptions(catalog, scope).levels).toEqual(["A级", "多等级", "未匹配"]);
  expect(
    catalogOptions(catalog, { ...scope, level: "多等级" }).products.map((p) => p.value),
  ).toEqual(["1", "2"]);
  expect(catalogOptions(catalog, { ...scope, level: "A级" }).products.map((p) => p.value)).toEqual([
    "0",
  ]);
  expect(
    catalogOptions(catalog, { ...scope, level: "未匹配" }).products.map((p) => p.value),
  ).toEqual(["3"]);
});

it("旧混合等级保留精确筛选并回显，搜索不丢失已选范围", () => {
  const selected = { ...scope, level: "A级/B级混合", products: ["1"] };
  const result = catalogOptions(catalog, selected);
  expect(result.levels).toContain("A级/B级混合");
  expect(result.selection.level).toBe("A级/B级混合");
  expect(result.products.map((p) => p.value)).toEqual(["1"]);
  const search = catalogOptions(catalog, selected, "不存在");
  expect(search.products).toEqual([]);
  expect(search.selection.products).toEqual(["1"]);
});

it("右侧关键词仍联动候选，左侧查找可临时覆盖候选搜索但不改变已选", () => {
  const selected = { ...scope, products: ["1", "2"], keyword: "商品1" };
  expect(catalogOptions(catalog, selected).products.map((p) => p.value)).toEqual(["1"]);
  const typing = catalogOptions(catalog, selected, "商品2");
  expect(typing.products.map((p) => p.value)).toEqual(["2"]);
  expect(typing.selection.products).toEqual(["1", "2"]);
  expect(selected.keyword).toBe("商品1");
  expect(catalogOptions(catalog, selected).products.map((p) => p.value)).toEqual(["1"]);
});

it("同维多选取并集，跨维筛选保留仍有效的下游项", () => {
  const input = {
    ...catalog,
    items: catalog.items.map((item, index) => ({
      ...item,
      operator: index < 2 ? "甲" : "乙",
      category: index === 0 || index === 2 ? "帽子" : "围巾",
    })),
  };
  const selected = {
    ...scope,
    operator: ["甲", "乙"],
    category: ["帽子", "不存在"],
    level: ["A级", "多等级"],
  };
  const result = catalogOptions(input, selected);
  expect(result.selection.category).toBe("帽子");
  expect(result.selection.level).toEqual(["A级", "多等级"]);
  expect(result.products.map((item) => item.value)).toEqual(["0", "2"]);
  expect(catalogOptions(input, { ...selected, operator: "乙" }).selection.level).toBe("多等级");
});
