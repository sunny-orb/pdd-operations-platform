import { expect, it } from "vitest";
import {
  centerCounts,
  productLink,
  promotionLink,
  aftersalesLink,
  shownAlerts,
  formatMetric,
} from "./model";

it("真实零与缺值分开展示，不在中心计算业务指标", () => {
  expect(formatMetric("0", "currency")).toBe("¥0");
  expect(formatMetric(null, "currency")).toBe("—");
  expect(formatMetric("0.0123", "percent")).toBe("1.23%");
  expect(formatMetric("9999999999999999.99", "currency")).toBe("¥10,000,000,000,000,000");
});
it("正式异常计数和负责人未确认，列表长度仅为观察项结构数", () => {
  const stats = centerCounts(
    [
      { id: "a", level: "observation" },
      { id: "b", level: "observation" },
    ],
    ["a"],
  );
  expect(stats).toEqual({
    exceptionCount: null,
    p0: null,
    p1: null,
    p2: null,
    assignees: null,
    observations: 2,
    actionRows: 1,
  });
  expect(shownAlerts([{ id: "a", level: "observation" }], "P0")).toEqual([]);
});
it("跨页携带已接收的完整K和真实报告，不猜不支持的对象", () => {
  const p = new URL(
    productLink("2026-10-27", { listing_id: "listing-a", shop_id: "663599807" }),
    "http://local",
  );
  expect(p.searchParams.get("main_listing")).toBe("listing-a");
  expect(p.searchParams.get("main_shop")).toBe("663599807");
  expect(p.searchParams.get("main_from")).toBe("2026-10-27");
  expect(p.hash).toBe("#risk");
  const a = new URL(
    promotionLink("2026-10-27", { listing_id: "listing-b", shop_id: "663599807" }),
    "http://local",
  );
  expect(a.searchParams.get("mode")).toBe("product");
  expect(a.searchParams.get("product_products")).toBe("listing-b");
  expect(a.hash).toBe("#promotion-diagnosis");
  expect(
    new URL(aftersalesLink("report-x", "帽子"), "http://local").searchParams.get("report_id"),
  ).toBe("report-x");
});
