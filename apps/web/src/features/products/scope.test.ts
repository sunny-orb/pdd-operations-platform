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

it("preserves invalid/reversed inputs and treats either single date as one day", async () => {
  const { scopeError } = await import("./scope");
  const reversed = readScope(
    new URLSearchParams("main_from=2026-10-20&main_to=2026-10-10"),
    "main",
    "2026-10-01",
    "2026-10-26",
  );
  expect(reversed.from).toBe("2026-10-20");
  expect(reversed.to).toBe("2026-10-10");
  expect(scopeError(reversed)).toContain("结束日期");
  for (const key of ["from", "to"]) {
    const single = readScope(
      new URLSearchParams(`main_${key}=2026-10-10`),
      "main",
      "2026-10-01",
      "2026-10-26",
    );
    expect([single.from, single.to]).toEqual(["2026-10-10", "2026-10-10"]);
  }
  expect(scopeError({ ...reversed, from: "2026-02-30", to: "2026-03-01" })).toContain("有效日期");
});
