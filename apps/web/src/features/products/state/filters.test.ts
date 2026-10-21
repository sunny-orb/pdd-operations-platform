import { describe, expect, it } from "vitest";
import {
  buildDefaultFilters,
  productKey,
  readFilters,
  updateFilters,
  writeFilters,
} from "./filters";

describe("商品筛选范围", () => {
  it("三套范围独立，保留入口参数与其他范围", () => {
    const params = new URLSearchParams("source=cockpit&days.shop=demo-outdoor");
    const next = writeFilters(params, "overview", {
      ...buildDefaultFilters(),
      shop: "demo-official",
    });
    expect(readFilters(next, "overview").shop).toBe("demo-official");
    expect(readFilters(next, "days").shop).toBe("demo-outdoor");
    expect(next.get("source")).toBe("cockpit");
  });
  it("跨店相同商品ID不合并", () => {
    expect(productKey("a", "001")).not.toBe(productKey("b", "001"));
    expect(productKey("a", "001")).toBe('["a","001"]');
  });
  it("筛选变化重置分页，翻页不重置", () => {
    const current = { ...buildDefaultFilters(), page: 4 };
    expect(updateFilters(current, { operator: "示例甲" }).page).toBe(1);
    expect(updateFilters(current, { page: 2 }).page).toBe(2);
  });
  it("非法URL分页不进入请求，商品多选可往返", () => {
    const filters = readFilters(new URLSearchParams("days.page=-1&days.pageSize=500"), "days");
    expect(filters.page).toBe(1);
    expect(filters.pageSize).toBe(100);
    const value = { ...buildDefaultFilters(), products: [productKey("a", "001")] };
    expect(
      readFilters(writeFilters(new URLSearchParams(), "days", value), "days").products,
    ).toEqual(value.products);
  });
});

it("显式清除日期保持空范围，不能被默认日期再次填回", () => {
  const cleared = writeFilters(new URLSearchParams("preview=1"), "overview", buildDefaultFilters());
  expect(cleared.has("overview.dateFrom")).toBe(true);
  expect(cleared.has("overview.dateTo")).toBe(true);
  expect(readFilters(cleared, "overview").dateFrom).toBe("");
});
