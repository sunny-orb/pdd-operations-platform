import { expect, it } from "vitest";
import {
  centerCounts,
  buildCenterView,
  actionFromObservation,
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

it("同对象来源合并、优先级取最高但原证据和不同建议保留，不加金额", () => {
  const product = observation({ sourcePriority: "P2", impact: "12", scope: "商品7日" });
  const promo = observation({
    id: "ad",
    domain: "promotion",
    sourcePriority: "P1",
    impact: "30",
    scope: "推广14日",
    link: "/promotion",
  });
  const actions = [
    actionFromObservation(product, "核查流量", "访客下降", "访客回升"),
    actionFromObservation(product, "核查流量", "访客下降", "访客回升"),
    actionFromObservation(promo, "调整投放", "投放低效", "ROI回升"),
  ];
  const result = buildCenterView([product, promo], actions, observed);
  expect(result.alerts).toHaveLength(1);
  expect(result.alerts[0].sourcePriority).toBe("P1");
  expect(
    result.alerts[0].members.map((x) => [x.sourcePriority, x.impact, x.scope, x.link]),
  ).toEqual([
    ["P2", "12", "商品7日", "/products"],
    ["P1", "30", "推广14日", "/promotion"],
  ]);
  expect(result.actions).toHaveLength(1);
  expect(result.actions[0].suggestions).toEqual(["核查流量", "调整投放"]);
  expect(result.actions[0].members).toHaveLength(3);
  expect(result.counts.actionRows).toBe(1);
  expect(result.sourceCounts).toEqual({ P0: 0, P1: 1, P2: 0, ungraded: 0 });
  expect(result.counts.p0).toBeNull();
});

it("完整身份跨店不串、未知身份分别保留且单列，未评级不伪造P2", () => {
  const rows = [
    observation(),
    observation({ id: "b", objectKey: '["listing","other","listing"]', sourcePriority: "P2" }),
    observation({ id: "c", objectKey: null, objectLevel: "unknown", sourcePriority: "观察" }),
    observation({ id: "d", objectKey: null, objectLevel: "unknown", sourcePriority: null }),
  ];
  const result = buildCenterView(
    rows,
    rows.map((x) => actionFromObservation(x, "核查", "待核", "复核")),
    observed,
  );
  expect(result.alerts).toHaveLength(4);
  expect(result.actions).toHaveLength(4);
  expect(result.counts.exceptionCount).toBe(2);
  expect(result.counts.unmappedObservations).toBe(2);
  expect(result.unmappedActions).toBe(2);
  expect(result.sourceCounts).toEqual({ P0: 1, P1: 0, P2: 1, ungraded: 0 });
  expect(result.alerts.slice(2).map((x) => x.sourcePriority)).toEqual([null, null]);
});

it("111条真实来源形态归并为110个对象建议，排序不改变各来源内容", () => {
  const rows = Array.from({ length: 110 }, (_, i) =>
    observation({
      id: `p${i}`,
      objectKey: `["listing","s","${i}"]`,
      sourcePriority: i === 0 ? "P0" : "P2",
    }),
  );
  rows.push({ ...rows[0], id: "ad", domain: "promotion", sourcePriority: "P1" });
  const result = buildCenterView(
    rows,
    rows.map((x) => actionFromObservation(x, "复核", "假设", "指标")),
    observed,
  );
  expect(result.counts).toMatchObject({ exceptionCount: 110, observations: 111, actionRows: 110 });
  expect(result.alerts).toHaveLength(110);
  expect(result.actions).toHaveLength(110);
  expect(result.sourceCounts).toEqual({ P0: 1, P1: 0, P2: 109, ungraded: 0 });
});

it("空建议不计动作；仅失败不补0，实际可评估空清单才计0", () => {
  expect(
    buildCenterView([observation()], [actionFromObservation(observation(), "  ", "", "")], observed)
      .actions,
  ).toEqual([]);
  const failed = [{ domain: "products" as const, status: "failed" as const }];
  expect(buildCenterView([], [], failed).sourceCounts).toBeNull();
  expect(buildCenterView([], [], failed).actionCount).toBeNull();
  expect(buildCenterView([], [], observed).sourceCounts).toEqual({
    P0: 0,
    P1: 0,
    P2: 0,
    ungraded: 0,
  });
  expect(buildCenterView([], [], observed).actionCount).toBe(0);
});

it("售后观察和动作逐项透传，不参与新的来源优先级或建议去重", () => {
  const row = observation({
    domain: "aftersales",
    objectKey: '["aftersales","r","overview","all"]',
    objectLevel: "overview",
  });
  const action = {
    ...actionFromObservation(row, "保留售后建议", "原原因", "原验证"),
    domain: undefined,
  };
  const result = buildCenterView(
    [row, { ...row, id: "after-2" }],
    [action, { ...action, id: "after-action-2" }],
    [{ domain: "aftersales", status: "observed" }],
  );
  expect(result.alerts).toHaveLength(2);
  expect(result.alerts[0].members[0]).toEqual(row);
  expect(result.actions).toHaveLength(2);
  expect(result.actions[0].members[0]).toEqual(action);
  expect(result.sourceCounts).toBeNull();
  expect(result.counts.p0).toBeNull();
});

it("中心逐项保留子看板异常与规则，不按规则状态、等级或版本二次筛掉来源行", () => {
  const rows = [
    observation({
      id: "p0",
      objectKey: '["listing","s","p0"]',
      sourcePriority: "P0",
      sourceRules: ["来源紧急规则"],
    }),
    observation({
      id: "p1",
      domain: "promotion",
      objectKey: '["listing","s","p1"]',
      sourcePriority: "P1",
      sourceRules: ["来源已触发规则"],
      ruleVersion: "sop-source-v1",
    }),
    observation({
      id: "obs",
      domain: "promotion",
      objectKey: '["listing","s","obs"]',
      sourcePriority: "观察",
    }),
  ];
  const view = buildCenterView(
    rows,
    rows.map((r) => actionFromObservation(r, "来源动作", "来源原因", "来源目标")),
    observed,
  );
  expect(view.alerts.flatMap((group) => group.members)).toEqual(rows);
  expect(view.actions).toHaveLength(3);
  expect(view.actions[1].members[0].context).toMatchObject({
    sourceRules: ["来源已触发规则"],
    ruleVersion: "sop-source-v1",
    scope: "各自来源窗",
  });
  expect(view.actions[1].members[0]).toMatchObject({
    assignee: "待分配",
    due: "待分配",
    reviewDate: "待分配",
    progress: "待核查",
  });
  expect(view.counts.p0).toBeNull();
});
