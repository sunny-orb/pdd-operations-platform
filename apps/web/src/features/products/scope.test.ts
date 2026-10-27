import { expect, it } from "vitest";
import { readScope, writeScope } from "./scope";
it("keeps overview, scores and detail independent, and full listing identities", () => {
  const p = new URLSearchParams("main_shop=one&score_category=冲锋衣&detail_page=2");
  const s = readScope(p, "main", "2026-10-01", "2026-10-26");
  expect(s.shop).toBe("one");
  const next = writeScope(p, "main", { ...s, products: ["shop-a-product-x", "shop-b-product-x"] });
  expect(next.get("score_category")).toBe("冲锋衣");
  expect(next.get("detail_page")).toBe("2");
  expect(next.getAll("main_listing")).toHaveLength(2);
});
