import { describe, expect, it } from "vitest";
import { dateError, readScope, toggleMetric, writeScope } from "./state";

describe("推广独立范围与最新日期", () => {
  it("未自选日期随每次元信息推进；其它筛选不冻结默认日期", () => {
    const search = writeScope(new URLSearchParams(), "store", { shop: "663599807" });
    expect(readScope(search, "store", "2026-09-27", "2026-10-26").to).toBe("2026-10-26");
    expect(readScope(search, "store", "2026-09-28", "2026-10-27").to).toBe("2026-10-27");
    expect(search.has("store_to")).toBe(false);
  });
  it("自选日期与模式筛选独立保留，不受最新版本覆盖", () => {
    const search = writeScope(new URLSearchParams(), "product", {
      from: "2026-09-01",
      to: "2026-09-15",
      products: ["listing:shop-a:id", "listing:shop-b:id"],
      page: 2,
    });
    expect(readScope(search, "product", "2026-09-28", "2026-10-27")).toMatchObject({
      from: "2026-09-01",
      to: "2026-09-15",
      products: ["listing:shop-a:id", "listing:shop-b:id"],
      page: 2,
    });
    expect(readScope(search, "store", "2026-09-28", "2026-10-27").products).toEqual([]);
  });
  it("店铺最多2项且至少1项；商品可选全部但不能清空", () => {
    expect(toggleMetric(["spend", "revenue"], "roi", "store")).toEqual(["revenue", "roi"]);
    expect(toggleMetric(["roi"], "roi", "store")).toEqual(["roi"]);
    expect(toggleMetric(["spend", "revenue"], "ctr", "product")).toEqual([
      "spend",
      "revenue",
      "ctr",
    ]);
    expect(toggleMetric(["ctr"], "ctr", "product")).toEqual(["ctr"]);
  });
  it("拒绝逆序/无效日期，不擅自截断到较慢源", () => {
    expect(dateError({ from: "2026-10-20", to: "2026-10-10" })).toContain("结束日期");
    expect(dateError({ from: "2026-02-30", to: "2026-10-28" })).toContain("有效");
    expect(dateError({ from: "2026-10-20", to: "2026-10-28" })).toBeNull();
  });
});
