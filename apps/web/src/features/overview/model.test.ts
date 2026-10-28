import { expect, it } from "vitest";
import {
  centerCounts,
  productLink,
  promotionLink,
  aftersalesLink,
  shownAlerts,
  formatMetric,
  listingObjectKey,
  aftersalesObjectKey,
  observationCoverage,
  salesLink,
  type Observation,
} from "./model";

it("真实零与缺值分开展示，不在中心计算业务指标", () => {
  expect(formatMetric("0", "currency")).toBe("¥0");
  expect(formatMetric(null, "currency")).toBe("—");
  expect(formatMetric("0.0123", "percent")).toBe("1.23%");
  expect(formatMetric("9999999999999999.99", "currency")).toBe("¥10,000,000,000,000,000");
});
const observation = (patch: Partial<Observation> = {}): Observation => ({
  id: "a",
  domain: "products",
  level: "observation",
  sourcePriority: "P0",
  objectKey: '["listing","s","listing"]',
  objectLevel: "listing",
  title: "同名商品",
  detail: "来源观察",
  scope: "各自来源窗",
  impact: "1",
  impactKind: "来源金额，不合计",
  link: "/products",
  ...patch,
});
const observed = [{ domain: "products" as const, status: "observed" as const }];
it("同一店铺链接跨商品推广多命中只计一个观察对象，不升级源域P0", () => {
  const stats = centerCounts(
    [
      observation(),
      observation({ id: "ad-a", domain: "promotion" }),
      observation({ id: "other-rule" }),
    ],
    ["a"],
    observed,
  );
  expect(stats.exceptionCount).toBe(1);
  expect(stats.observations).toBe(3);
  expect(stats.actionRows).toBe(1);
  expect(stats.countStatus).toBe("observed");
  expect([stats.p0, stats.p1, stats.p2, stats.assignees]).toEqual([null, null, null, null]);
  expect(shownAlerts([{ id: "a", level: "observation" }], "P0")).toEqual([]);
});
it("不同店同商品ID及同品类商品不合并，不能按标题猜对象", () => {
  const otherShop = observation({
    objectKey: listingObjectKey({ shop_id: "other", listing_id: "listing" }),
  });
  const otherListing = observation({
    objectKey: listingObjectKey({ shop_id: "s", listing_id: "listing-2" }),
  });
  const stats = centerCounts([observation(), otherShop, otherListing], [], observed);
  expect(stats.exceptionCount).toBe(3);
});
it("售后同报告总览与品类分别计项，重复品类命中归并，其他报告不串", () => {
  const rows = [
    observation({
      domain: "aftersales",
      objectLevel: "overview",
      objectKey: aftersalesObjectKey("report", "overview", "all"),
    }),
    observation({
      domain: "aftersales",
      objectLevel: "category",
      objectKey: aftersalesObjectKey("report", "category", "帽子"),
    }),
    observation({
      domain: "aftersales",
      objectLevel: "category",
      objectKey: aftersalesObjectKey("report", "category", "帽子"),
    }),
    observation({
      domain: "aftersales",
      objectLevel: "category",
      objectKey: aftersalesObjectKey("other-report", "category", "帽子"),
    }),
  ];
  expect(centerCounts(rows, [], observed).exceptionCount).toBe(3);
});
it("缺完整对象键不猜映射，已映射小计与未映射条数分别表达", () => {
  expect(listingObjectKey({ shop_id: null, listing_id: "listing" })).toBeNull();
  const unknown = observation({ objectKey: null, objectLevel: "unknown" });
  const partial = centerCounts([observation(), unknown], [], observed);
  expect(partial.exceptionCount).toBe(1);
  expect(partial.unmappedObservations).toBe(1);
  expect(partial.countStatus).toBe("partial_mapping");
  expect(centerCounts([unknown], [], observed).exceptionCount).toBeNull();
});
it("成功取得空观察列表是已接范围零，失败或未接接口不能静默算零", () => {
  const unavailable = observationCoverage([
    { id: "products", data: undefined, error: "查询失败", loading: false },
  ]);
  expect(centerCounts([], [], unavailable).exceptionCount).toBeNull();
  expect(unavailable[0].status).toBe("failed");
  const zero = centerCounts([], [], [...observed, ...unavailable]);
  expect(zero.exceptionCount).toBe(0);
  expect(zero.availableDomains).toBe(1);
  expect(zero.unavailableDomains).toBe(1);
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
  const s = new URL(salesLink("2026-10", "2026-10-27"), "http://local");
  expect([...s.searchParams]).toEqual([
    ["month", "2026-10"],
    ["from", "2026-10-27"],
    ["to", "2026-10-27"],
  ]);
  expect(s.hash).toBe("#performance");
});
