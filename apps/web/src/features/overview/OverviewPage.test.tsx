// @vitest-environment jsdom
import { cleanup, fireEvent, render as renderView, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { OverviewPage } from "./OverviewPage";
import { useOverview } from "./useOverview";
import { actionFromObservation, productLink, type Observation } from "./model";
import type { DomainState } from "./data";
import { useWeather } from "./weather";
import { RegisterScope } from "../../shared/assistant/scopeContext";
vi.mock("./useOverview", () => ({ useOverview: vi.fn() }));
vi.mock("./weather", () => ({
  useWeather: vi.fn(() => ({
    meta: undefined,
    publication: undefined,
    selectedId: "",
    selection: "latest",
    options: [],
    select: vi.fn(),
    refresh: vi.fn(),
    loading: false,
    refreshing: false,
    error: "天气暂不可用",
  })),
}));
vi.mock("../../shared/layouts/DashboardLayout", () => ({
  DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: () => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return true;
      },
    }),
  });
});
beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          data: { sources: [], processing: [], monitor: { enabled: true, interval_seconds: 900 } },
          meta: { generated_at: "2026-11-10T00:00:00Z" },
        }),
      ),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function render(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderView(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}
const obs: Observation = {
  id: "x",
  domain: "products",
  level: "observation",
  sourcePriority: "P0",
  objectKey: '["listing","source-shop","listing-id"]',
  objectLevel: "listing",
  title: "单款观察提示",
  detail: "待核完整性",
  scope: "商店 · 风险观察窗",
  impact: "0",
  impactKind: "规则估算影响，非已实现损失",
  link: productLink("2026-10-27", { listing_id: "listing-id", shop_id: "source-shop" }),
};
const states = (): DomainState[] =>
  ["sales", "products", "promotion", "aftersales"].map((id, i) => ({
    id: id as DomainState["id"],
    meta: {
      id: id as DomainState["id"],
      version: `v${i}`,
      date: "2026-10-27",
      completeThrough: null,
      success: null,
      attempt: null,
      attemptStatus: null,
      status: "partial",
      notes: [],
      raw: null,
    },
    data: {
      card: {
        title: ["店铺销售 GMV", "商品数据", "推广效率", "售后退款"][i],
        heading: "来源真实卡片",
        value: i === 1 ? "0" : null,
        valueType: "currency",
        valueLabel: "观察单日",
        pairs: [],
        note: "完整性待核验",
        link: "/sales",
        status: "观察",
      },
      observations: i === 1 ? [obs] : [],
      observationState: i === 0 ? "not_implemented" : "observed",
      actions: i === 1 ? [actionFromObservation(obs, "复核源域事实", "待验证", "复核变化")] : [],
    },
    loading: false,
    refreshing: false,
    error: null,
    refresh: vi.fn(),
  }));
it("运营中心向助手绑定四域实际版本，推广版本归到店铺推广域", () => {
  vi.mocked(useOverview).mockReturnValue(states());
  const register = vi.fn();
  render(
    <MemoryRouter>
      <RegisterScope.Provider value={register}>
        <OverviewPage />
      </RegisterScope.Provider>
    </MemoryRouter>,
  );
  expect(register).toHaveBeenLastCalledWith({
    page: "运营看板中心",
    subject: null,
    context: {
      domain: "all",
      versions: { sales: "v0", products: "v1", promotion_store: "v2", aftersales: "v3" },
    },
  });
});
it("顶部统计全部预警及真实建议，默认完整清单分页，筛选与翻页不改总数", () => {
  const data = states();
  const items = Array.from({ length: 110 }, (_, i) => ({
    ...obs,
    id: `full-${i}`,
    objectKey: JSON.stringify(["listing", "source-shop", `full-${i}`]),
    title: `全量提示${i + 1}`,
  }));
  data[1].data!.observations = items;
  // 一项没有来源建议，预警与待办分别统计，不补建议凑数。
  data[1].data!.actions = items
    .filter((_, i) => i !== 1)
    .map((item) => actionFromObservation(item, "来源建议", "来源原因", "来源验证"));
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const summary = screen.getByLabelText("经营总览关键数据");
  const alerts = document.getElementById("alerts")!;
  const actions = document.getElementById("actions")!;
  expect(summary.textContent).toContain("异常数量110 项");
  expect(summary.textContent).toContain("待办动作109 项");
  expect(document.getElementById("overview")!.textContent).toContain("待办动作 109 项");
  expect(
    within(within(alerts).getByRole("list", { name: "预警列表" })).getAllByRole("listitem"),
  ).toHaveLength(6);
  expect(
    within(actions).getByRole("table").querySelectorAll("tbody tr[data-row-key]"),
  ).toHaveLength(6);
  expect(within(alerts).getByRole("navigation", { name: "预警分页" }).textContent).toContain(
    "共 110 条",
  );
  expect(within(actions).getByRole("navigation", { name: "动作分页" }).textContent).toContain(
    "共 109 条",
  );
  expect(within(alerts).getByRole("group", { name: "来源优先级" })).toBeTruthy();
  expect(within(alerts).queryByRole("button", { name: /首页关注|查看全部预警/ })).toBeNull();
  expect(within(actions).queryByRole("button", { name: /首页建议|查看全部建议/ })).toBeNull();
  fireEvent.click(within(alerts).getByRole("button", { name: "P1" }));
  expect(within(alerts).queryAllByRole("listitem")).toHaveLength(0);
  expect(summary.textContent).toContain("异常数量110 项");
  expect(summary.textContent).toContain("待办动作109 项");
  fireEvent.click(within(alerts).getByRole("button", { name: "全部" }));
  fireEvent.click(within(alerts).getByTitle("2"));
  expect(within(alerts).getByText("全量提示7", { exact: true })).toBeTruthy();
  const attentionLink = within(summary).getByRole("link", { name: "异常数量110 项" });
  expect(attentionLink.getAttribute("href")).toBe("#alerts");
  fireEvent.click(attentionLink);
  expect(within(alerts).getByText("全量提示7", { exact: true })).toBeTruthy();
  fireEvent.click(within(actions).getByTitle("2"));
  expect(within(actions).getByRole("link", { name: "全量提示8" })).toBeTruthy();
  const actionLink = within(summary).getByRole("link", { name: "待办动作109 项" });
  expect(actionLink.getAttribute("href")).toBe("#actions");
  fireEvent.click(actionLink);
  expect(within(actions).getByRole("link", { name: "全量提示8" })).toBeTruthy();
  const heroAction = within(document.getElementById("overview")!).getByRole("link", {
    name: /待办动作 109 项/,
  });
  expect(heroAction.getAttribute("href")).toBe("#actions");
  data[1].data!.observations = items.slice(7, 9);
  data[1].data!.actions = data[1].data!.actions.slice(6, 8);
  rerender(view());
  expect(summary.textContent).toContain("异常数量2 项");
  expect(summary.textContent).toContain("待办动作2 项");
  expect(within(alerts).getByText("全量提示8", { exact: true })).toBeTruthy();
  expect(within(actions).getByRole("link", { name: "全量提示8" })).toBeTruthy();
  data[1].data!.observations = [];
  data[1].data!.actions = [];
  rerender(view());
  expect(summary.textContent).toContain("异常数量0 项");
  expect(summary.textContent).toContain("待办动作0 项");
  expect(data[1].refresh).not.toHaveBeenCalled();
});
it("同对象跨域只展示一行，双来源入口保留，筛选及动态更新与售后独立", () => {
  const data = states();
  const promo: Observation = {
    ...obs,
    id: "promo",
    domain: "promotion",
    sourcePriority: "P1",
    title: "推广问题",
    scope: "推广诊断窗",
    impact: "25",
    link: "/promotion?mode=product&product_shop=source-shop&product_products=listing-id#promotion-diagnosis",
  };
  data[2].data!.observations = [promo];
  data[2].data!.actions = [actionFromObservation(promo, "调整预算", "预算低效", "复核ROI")];
  data[3].data!.card.value = "0.3994";
  data[3].data!.card.valueType = "percent";
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const alerts = document.getElementById("alerts")!;
  expect(within(alerts).getAllByRole("listitem")).toHaveLength(1);
  expect(within(alerts).getByRole("link", { name: "商品数据 →" }).getAttribute("href")).toContain(
    "main_shop=source-shop&main_listing=listing-id",
  );
  expect(within(alerts).getByRole("link", { name: "推广效率 →" }).getAttribute("href")).toBe(
    promo.link,
  );
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(table.textContent).toContain("复核源域事实；调整预算");
  expect(screen.getByLabelText("经营总览关键数据").textContent).toContain("异常数量1 项");
  expect(screen.getByLabelText("经营总览关键数据").textContent).toContain("待办动作1 项");
  expect(within(document.getElementById("overview")!).getByText("1 项")).toBeTruthy();
  expect(document.querySelector(".m6-risk-callout")?.textContent).toContain("1 项 P0 风险");
  expect(document.querySelector(".m6-risk-callout")?.textContent).toContain("39.94%");
  fireEvent.click(within(alerts).getByRole("button", { name: "P1" }));
  expect(within(alerts).queryAllByRole("listitem")).toHaveLength(0);
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  data[1].data!.observations = [];
  data[1].data!.actions = [];
  rerender(view());
  expect(within(alerts).getAllByRole("listitem")).toHaveLength(1);
  expect(table.textContent).not.toContain("复核源域事实");
  expect(table.textContent).toContain("调整预算");
  expect(document.querySelector(".m6-risk-callout")?.textContent).toContain("39.94%");
});
it("多窗口同对象固定一行，金额摘要受限且原始金额和全部入口可展开", async () => {
  const data = states();
  data[1].data!.observations = Array.from({ length: 5 }, (_, i) => ({
    ...obs,
    id: `window-${i}`,
    scope: `窗口${i}`,
    impact: String(i + 1),
    link: `/products?main_from=2026-10-${20 + i}#risk`,
  }));
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const list = screen.getByRole("list", { name: "预警列表" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(1);
  expect(list.querySelectorAll(".m6-impact b")).toHaveLength(2);
  fireEvent.click(within(list).getByRole("button", { name: "全部入口（5）" }));
  expect((await screen.findAllByRole("link", { name: "商品数据 →" })).length).toBe(5);
  expect(
    screen.getAllByRole("link", { name: "商品数据 →" }).map((link) => link.getAttribute("href")),
  ).toContain("/products?main_from=2026-10-24#risk");
});
it("全部来源失败时建议统计不补零，有待核域真实观察时范围也保留", () => {
  const data = states();
  for (const state of data) {
    state.data = undefined;
    state.error = "查询失败";
  }
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const definitions = document.getElementById("definitions")!;
  expect(definitions.textContent).toContain("— 条来源建议 · — 条归并建议");
  expect(screen.getByLabelText("经营总览关键数据").textContent).toContain("异常数量— 项");
  expect(screen.getByLabelText("经营总览关键数据").textContent).toContain("待办动作— 项");
  expect(document.getElementById("actions")!.textContent).toContain("当前清单 — 条归并建议");
  data[1] = states()[1];
  data[2] = states()[2];
  data[2].data!.observationState = "pending_validation";
  data[2].data!.observations = [
    { ...obs, id: "pending", domain: "promotion", objectKey: '["listing","s2","l2"]' },
  ];
  rerender(view());
  expect(screen.getByLabelText("经营总览关键数据").textContent).toContain("异常数量2 项");
});
it("保留原型顺序和八列动作，正式数未知、建议不冒充已派单", () => {
  vi.mocked(useOverview).mockReturnValue(states());
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const ids = [
    ...document.querySelectorAll("#overview,#alerts,#actions,#weather,#definitions"),
  ].map((e) => e.id);
  expect(ids).toEqual(["overview", "alerts", "actions", "weather", "definitions"]);
  expect(screen.getByText("本日负责人")).toBeTruthy();
  expect(screen.getByText("异常数量")).toBeTruthy();
  expect(screen.queryByText(/来源优先级 · P0/)).toBeNull();
  const table = screen.getByRole("table");
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((x) => x.textContent),
  ).toEqual(["问题", "影响金额", "原因假设", "动作", "负责人", "截止时间", "当前进度", "验证指标"]);
  expect(within(table).getAllByText("待分配")[0]).toBeTruthy();
  expect(within(table).getByText("待核查")).toBeTruthy();
  expect(screen.getByText("天气暂不可用")).toBeTruthy();
});
it("P0筛选只改预警列表，不改原值和建议；跳转带真实K和日期", () => {
  vi.mocked(useOverview).mockReturnValue(states());
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const alerts = document.getElementById("alerts")!;
  const jump = within(alerts).getByRole("link", { name: "定位处理 →" });
  expect(jump.getAttribute("href")).toContain("main_shop=source-shop&main_listing=listing-id#risk");
  expect(within(alerts).getByText(/商品数据 · P0/)).toBeTruthy();
  fireEvent.click(within(alerts).getByRole("button", { name: "P0" }));
  expect(within(alerts).getByText("单款观察提示")).toBeTruthy();
  expect(screen.getByRole("table").textContent).toContain("复核源域事实");
  expect(screen.getAllByText("¥0").length).toBeGreaterThan(0);
});
it("某域失败仍展示其他来源，缺失不伪装为零", () => {
  const data = states();
  data[0] = { ...data[0], data: undefined, error: "销售查询失败" };
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  expect(screen.getByText("销售查询失败")).toBeTruthy();
  expect(document.getElementById("domain-sales")?.textContent).toContain("—");
  expect(document.getElementById("domain-products")?.textContent).toContain("¥0");
});
it("预警每页六条，翻页保留定位参数，切换等级回到首页且不改本日动作", () => {
  const data = states();
  data[1].data!.observations = Array.from({ length: 13 }, (_, i) => ({
    ...obs,
    id: `observation-${i + 1}`,
    objectKey: JSON.stringify(["listing", "source-shop", `listing-${i + 1}`]),
    title: `经营提示${i + 1}`,
    link: productLink("2026-11-01", { shop_id: "source-shop", listing_id: `listing-${i + 1}` }),
  }));
  data[1].data!.actions = [
    actionFromObservation(data[1].data!.observations[0], "复核源域事实", "来源原因", "来源验证"),
  ];
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const alerts = document.getElementById("alerts")!;
  expect(within(alerts).getAllByRole("link", { name: "定位处理 →" })).toHaveLength(6);
  expect(within(alerts).queryByText("经营提示7", { exact: true })).toBeNull();
  fireEvent.click(within(alerts).getByTitle("2"));
  expect(within(alerts).getByText("经营提示7", { exact: true })).toBeTruthy();
  expect(within(alerts).queryByText("经营提示1", { exact: true })).toBeNull();
  expect(
    within(alerts).getAllByRole("link", { name: "定位处理 →" })[0].getAttribute("href"),
  ).toContain("main_listing=listing-7#risk");
  fireEvent.click(within(alerts).getByTitle("3"));
  expect(within(alerts).getAllByRole("link", { name: "定位处理 →" })).toHaveLength(1);
  expect(within(alerts).getByText("经营提示13", { exact: true })).toBeTruthy();
  fireEvent.click(within(alerts).getByRole("button", { name: "P0" }));
  expect(within(alerts).getAllByRole("link", { name: "定位处理 →" })).toHaveLength(6);
  fireEvent.click(within(alerts).getByText("全部", { exact: true }));
  expect(within(alerts).getByText("经营提示1", { exact: true })).toBeTruthy();
  expect(within(alerts).getAllByRole("link", { name: "定位处理 →" })).toHaveLength(6);
  expect(screen.getByRole("table").textContent).toContain("复核源域事实");
});
it("预警六条以内不显示分页，动态缩页不落到空白页", () => {
  const data = states();
  const items = Array.from({ length: 7 }, (_, i) => ({
    ...obs,
    id: `obs-${i}`,
    objectKey: JSON.stringify(["listing", "source-shop", `listing-${i + 1}`]),
    title: `动态提示${i + 1}`,
  }));
  data[1].data!.observations = items.slice(0, 6);
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const alerts = document.getElementById("alerts")!;
  expect(within(alerts).queryByRole("navigation", { name: "预警分页" })).toBeNull();
  data[1].data!.observations = items;
  rerender(view());
  fireEvent.click(within(alerts).getByTitle("2"));
  expect(within(alerts).getByText("动态提示7", { exact: true })).toBeTruthy();
  data[1].data!.observations = items.slice(0, 2);
  rerender(view());
  expect(within(alerts).getAllByRole("link", { name: "定位处理 →" })).toHaveLength(2);
  expect(within(alerts).getByText("动态提示1", { exact: true })).toBeTruthy();
  expect(within(alerts).queryByRole("navigation", { name: "预警分页" })).toBeNull();
  data[1].data!.observations = items;
  rerender(view());
  expect(within(alerts).getByText("动态提示1", { exact: true })).toBeTruthy();
  expect(within(alerts).queryByText("动态提示7", { exact: true })).toBeNull();
  data[1].data!.observations = [];
  rerender(view());
  expect(within(alerts).queryByRole("link", { name: "定位处理 →" })).toBeNull();
  expect(within(alerts).getByText(/暂无已取得的观察提示/)).toBeTruthy();
});
it("本日动作每页六条，保留八列及定位参数，翻页不改变全量待办数", () => {
  const data = states();
  data[1].data!.actions = Array.from({ length: 13 }, (_, i) =>
    actionFromObservation(
      {
        ...obs,
        id: `action-${i + 1}`,
        objectKey: JSON.stringify(["listing", "source-shop", `listing-${i + 1}`]),
        title: `经营动作${i + 1}`,
        impact: i === 1 ? null : "0",
        link: productLink("2026-11-01", { shop_id: "source-shop", listing_id: `listing-${i + 1}` }),
      },
      "核对源域事实",
      "核验经营变化",
      "复核指标",
    ),
  );
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const actions = document.getElementById("actions")!;
  const table = within(actions).getByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(7);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
  expect(within(table).getAllByText("¥0").length).toBeGreaterThan(0);
  expect(within(table).getByText("—")).toBeTruthy();
  expect(within(table).queryByRole("link", { name: "经营动作7" })).toBeNull();
  fireEvent.click(within(actions).getByTitle("2"));
  expect(within(table).getAllByRole("row")).toHaveLength(7);
  expect(within(table).getByRole("link", { name: "经营动作7" }).getAttribute("href")).toContain(
    "main_listing=listing-7#risk",
  );
  expect(within(table).queryByRole("link", { name: "经营动作1" })).toBeNull();
  fireEvent.click(within(actions).getByTitle("3"));
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(within(table).getByRole("link", { name: "经营动作13" })).toBeTruthy();
  expect(within(actions).getByRole("navigation", { name: "动作分页" }).textContent).toContain(
    "共 13 条",
  );
  expect(within(document.getElementById("overview")!).getByText("13 项")).toBeTruthy();
  expect(within(document.getElementById("alerts")!).getByText(obs.title)).toBeTruthy();
});
it("本日动作六条以内不分页，动态缩页再增长不回跳，空结果不补经营行", () => {
  const data = states();
  const items = Array.from({ length: 7 }, (_, i) =>
    actionFromObservation(
      {
        ...obs,
        id: `daily-${i + 1}`,
        objectKey: JSON.stringify(["listing", "source-shop", `listing-${i + 1}`]),
        title: `动态动作${i + 1}`,
      },
      "核对原因",
      "核对动作",
      "验证指标",
    ),
  );
  data[1].data!.actions = items.slice(0, 6);
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const actions = document.getElementById("actions")!;
  expect(within(actions).queryByRole("navigation", { name: "动作分页" })).toBeNull();
  data[1].data!.actions = items;
  rerender(view());
  fireEvent.click(within(actions).getByTitle("2"));
  expect(within(actions).getByRole("link", { name: "动态动作7" })).toBeTruthy();
  data[1].data!.actions = items.slice(0, 2);
  rerender(view());
  expect(within(actions).getByRole("link", { name: "动态动作1" })).toBeTruthy();
  expect(within(actions).queryByRole("navigation", { name: "动作分页" })).toBeNull();
  data[1].data!.actions = items;
  rerender(view());
  expect(within(actions).getByRole("link", { name: "动态动作1" })).toBeTruthy();
  expect(within(actions).queryByRole("link", { name: "动态动作7" })).toBeNull();
  data[1].data!.actions = [];
  rerender(view());
  expect(within(actions).queryByRole("link")).toBeNull();
  expect(within(actions).getByText("尚未取得可核来源建议")).toBeTruthy();
});
it("经营卡随结果更新涨跌和缺值，技术长说明只在口径展开中读取", async () => {
  const data = states();
  data[1].data!.card.pairs = [
    { label: "日环比", value: "0.148", type: "percent" },
    { label: "同比", value: "-0.023", type: "percent" },
    { label: "观察异常对象", value: "2", type: "number" },
    { label: "最近更新", value: "2026-11-01", type: "text" },
  ];
  data[1].data!.card.note = "此版本的详细来源核查记录";
  data[1].data!.card.link = "/products?main_from=2026-11-01&main_to=2026-11-01#risk";
  vi.mocked(useOverview).mockReturnValue(data);
  const { rerender } = render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const card = document.getElementById("domain-products")!;
  expect(within(card).getByText("+14.8%")).toBeTruthy();
  expect(within(card).getByText("-2.3%")).toBeTruthy();
  expect(within(card).getByText("2 项")).toBeTruthy();
  expect(within(card).getByRole("link", { name: "进入处理 →" }).getAttribute("href")).toBe(
    "/products?main_from=2026-11-01&main_to=2026-11-01#risk",
  );
  expect(within(card).queryByText("此版本的详细来源核查记录")).toBeNull();
  expect(screen.queryByText("此版本的详细来源核查记录")).toBeNull();
  expect(screen.queryByText("此版本的详细来源核查记录")).toBeNull();
  fireEvent.click(screen.getByText("详细来源与更新记录"));
  expect(await screen.findByText("此版本的详细来源核查记录")).toBeTruthy();
  data[1].data!.card.pairs[0].value = null;
  data[1].data!.card.pairs[1].value = "0";
  data[1].data!.card.pairs[2].value = "0";
  rerender(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  expect(within(card).queryByText("+14.8%")).toBeNull();
  expect(within(card).getByText("0.0%")).toBeTruthy();
  expect(within(card).getByText("0 项")).toBeTruthy();
  expect(within(card).getByText("—")).toBeTruthy();
});
it("顶部摘要随发布结果更新，快捷入口保留各域真实期间与定位参数", () => {
  const data = states();
  data[1].meta!.date = "2026-10-26";
  data[2].meta!.date = "2026-10-25";
  data[3].meta!.date = "2026-10-24";
  data[0].data!.card.value = "330127";
  data[0].data!.card.pairs = [{ label: "目标达成", value: "0.041", type: "percent" }];
  data[0].data!.card.link = "/sales?month=2026-10&from=2026-10-27&to=2026-10-27#performance";
  data[1].data!.card.link = "/products?main_from=2026-10-27&main_to=2026-10-27#risk";
  data[2].data!.card.link =
    "/promotion?mode=product&product_from=2026-10-27&product_to=2026-10-27#promotion-diagnosis";
  data[3].data!.card.link = "/aftersales?report_id=report-oct#aftersales-analysis";
  vi.mocked(useOverview).mockReturnValue(data);
  const { rerender } = render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const hero = document.getElementById("overview")!;
  expect(screen.getByLabelText("数据更新时间和口径入口").textContent).toContain(
    "店铺销售 2026-10-27 · 商品数据 2026-10-26 · 推广效率 2026-10-25 · 售后退款 2026-10-24",
  );
  expect(within(hero).getByText("¥330,127")).toBeTruthy();
  expect(within(hero).getByText("4.1%")).toBeTruthy();
  expect(
    within(hero)
      .getByRole("link", { name: /查看 GMV 原始数据/ })
      .getAttribute("href"),
  ).toBe("/sales?month=2026-10&from=2026-10-27&to=2026-10-27#performance");
  expect(
    within(hero)
      .getByRole("link", { name: /进入商品数据看板/ })
      .getAttribute("href"),
  ).toBe("/products?main_from=2026-10-27&main_to=2026-10-27#risk");
  expect(
    within(hero)
      .getByRole("link", { name: /进入推广数据看板/ })
      .getAttribute("href"),
  ).toBe(
    "/promotion?mode=product&product_from=2026-10-27&product_to=2026-10-27#promotion-diagnosis",
  );
  expect(
    within(hero)
      .getByRole("link", { name: /进入退货分析看板/ })
      .getAttribute("href"),
  ).toBe("/aftersales?report_id=report-oct#aftersales-analysis");
  expect(
    within(hero)
      .getByRole("link", { name: /查看预警与处理建议/ })
      .getAttribute("href"),
  ).toBe("#alerts");
  data[0].data!.card.value = "0";
  data[0].data!.card.pairs[0].value = null;
  data[0].meta!.date = "2026-11-01";
  data[0].data!.card.link = "/sales?month=2026-11&from=2026-11-01&to=2026-11-01#performance";
  data[1].data!.observations = [];
  data[1].data!.actions = [];
  rerender(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  expect(within(hero).getByText("¥0")).toBeTruthy();
  expect(within(hero).queryByText("4.1%")).toBeNull();
  expect(within(hero).getByText("0 项")).toBeTruthy();
  expect(
    within(hero)
      .getByRole("link", { name: /查看 GMV 原始数据/ })
      .getAttribute("href"),
  ).toContain("month=2026-11&from=2026-11-01");
  expect(screen.getByLabelText("数据更新时间和口径入口").textContent).toContain(
    "店铺销售 2026-11-01",
  );
});
it("口径摘要直接展示，只有详细来源默认折叠，刷新仍只调用本地查询", async () => {
  const data = states();
  data[0].data!.card.valueLabel = "11 月累计 GMV";
  data[0].data!.card.link = "/sales?month=2026-11#performance";
  data[1].data!.card.link = "/products?main_from=2026-11-01&main_to=2026-11-01#risk";
  data[2].data!.card.link = "/promotion?mode=product&product_from=2026-11-01#promotion-diagnosis";
  data[3].data!.card.link = "/aftersales?report_id=report-nov#aftersales-analysis";
  data[1].data!.card.note = "本发布版完整来源记录";
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const definitions = document.getElementById("definitions")!;
  expect(within(definitions).queryByRole("button", { name: /统一筛选与指标口径/ })).toBeNull();
  expect(within(definitions).getAllByRole("button")).toHaveLength(1);
  expect(within(definitions).queryByText("本发布版完整来源记录")).toBeNull();
  for (const name of ["时间与范围", "各域口径", "预警与动作"]) {
    expect(within(definitions).getByRole("heading", { name })).toBeTruthy();
  }
  expect(within(definitions).getByText("11 月累计 GMV")).toBeTruthy();
  const source = within(definitions).getByRole("button", { name: /详细来源与更新记录/ });
  expect(source.getAttribute("aria-expanded")).toBe("false");
  expect(within(definitions).queryByText("本发布版完整来源记录")).toBeNull();
  for (const [index, name] of ["店铺销售", "商品数据", "推广效率", "售后退款"].entries()) {
    expect(within(definitions).getByRole("link", { name }).getAttribute("href")).toBe(
      data[index].data!.card.link,
    );
  }
  fireEvent.click(source);
  expect(source.getAttribute("aria-expanded")).toBe("true");
  expect(await within(definitions).findByText("本发布版完整来源记录")).toBeTruthy();
  for (const item of data) expect(item.refresh).not.toHaveBeenCalled();
  fireEvent.click(within(definitions).getByRole("button", { name: "刷新本地查询" }));
  for (const item of data) expect(item.refresh).toHaveBeenCalledOnce();
  fireEvent.click(source);
  expect(source.getAttribute("aria-expanded")).toBe("false");
});
it("口径覆盖随完整结果更新，证据未齐显示未知而不是零", () => {
  const data = states();
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const coverage = screen.getByLabelText("当前观察覆盖");
  expect(within(coverage).getByText("1 条提示")).toBeTruthy();
  expect(within(coverage).getByText("1 个对象")).toBeTruthy();
  data[1].data!.observations = [
    obs,
    { ...obs, id: "second", objectKey: '["listing","other-shop","other-listing"]' },
  ];
  rerender(view());
  expect(within(coverage).getByText("2 条提示")).toBeTruthy();
  expect(within(coverage).getByText("2 个对象")).toBeTruthy();
  for (const item of data.slice(1)) {
    item.data!.observations = [];
    item.data!.observationState = "pending_validation";
  }
  rerender(view());
  expect(within(coverage).getByText("— 条提示")).toBeTruthy();
  expect(within(coverage).getByText("— 个对象")).toBeTruthy();
  data[1].data!.observationState = "observed";
  rerender(view());
  expect(within(coverage).getByText("0 条提示")).toBeTruthy();
  expect(within(coverage).getByText("0 个对象")).toBeTruthy();
});
it("天气200部分解析与失败保旧具有明确状态和原因", () => {
  const state = vi.mocked(useWeather)();
  vi.mocked(useWeather).mockReturnValue({
    ...state,
    error: null,
    meta: {
      data_version: "w",
      status: "partial",
      default_publication_id: "weather",
      publications: [],
      last_success_at: null,
      last_attempt_at: null,
      last_attempt_status: "failed",
      error_code: "SOURCE_TIMEOUT",
    },
  });
  vi.mocked(useOverview).mockReturnValue(states());
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  expect(screen.queryByText("天气部分字段待核")).toBeNull();
  expect(screen.getByText(/SOURCE_TIMEOUT/)).toBeTruthy();
});

it("动作详情只展示来源规则与证据，未指派负责人和日期不编造", async () => {
  const data = states();
  data[1].data!.actions = [
    actionFromObservation(
      { ...obs, sourceRules: ["来源规则原文"], ruleVersion: "source-v1" },
      "来源动作",
      "来源假设",
      "来源目标",
    ),
  ];
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const actions = document.getElementById("actions")!;
  expect(within(actions).getByText("待核查")).toBeTruthy();
  fireEvent.click(within(actions).getByRole("button", { name: "查看详情" }));
  const detail = await screen.findByRole("dialog");
  expect(detail.textContent).toContain("source-v1");
  expect(detail.textContent).toContain("来源规则原文");
  expect(detail.textContent).toContain("商店 · 风险观察窗");
  expect(detail.textContent).toContain("待分配 / 待分配 / 待分配");
  expect(detail.textContent).toContain("来源目标");
  expect(detail.textContent).toContain("待核查 / 尚未登记");
  expect(detail.textContent).not.toContain("已安排");
  expect(within(detail).getByRole("link", { name: "查看来源证据 →" }).getAttribute("href")).toBe(
    obs.link,
  );
});

it("P0摘要使用全量来源去重计数而非六项预览，更新后保留真实零及不可用区别", () => {
  const data = states();
  data[1].data!.observations = Array.from({ length: 8 }, (_, i) => ({
    ...obs,
    id: `p0-${i}`,
    objectKey: JSON.stringify(["listing", "shop", String(i)]),
  }));
  data[2].data!.observations = [
    { ...data[1].data!.observations[0], id: "promo", domain: "promotion" },
  ];
  // 售后本轮不参与来源P0汇总，退款率照原值。
  data[3].data!.observations = [{ ...obs, id: "after", domain: "aftersales", objectKey: "report" }];
  data[3].data!.card.value = "0.3994";
  vi.mocked(useOverview).mockReturnValue(data);
  const page = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(page());
  const callout = document.querySelector(".m6-risk-callout")!;
  expect(callout.textContent).toContain("8 项 P0 风险");
  expect(callout.textContent).toContain("商品 / 推广已接范围 · 来源等级");
  expect(callout.textContent).toContain("39.94%");
  data[1].data!.observations = [];
  data[2].data!.observations = [];
  rerender(page());
  expect(callout.textContent).toContain("0 项 P0 风险");
  for (const state of data.slice(1, 3)) state.data!.observationState = "pending_validation";
  rerender(page());
  expect(callout.textContent).toContain("— 项 P0 风险");
  expect(callout.textContent).toContain("39.94%");
});

it("经营卡显示来源最高等级，更新时切换P0/P1/P2；空名单及售后不伪造等级", () => {
  const data = states();
  data[3].data!.observations = [{ ...obs, domain: "aftersales" }];
  vi.mocked(useOverview).mockReturnValue(data);
  const view = () => (
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>
  );
  const { rerender } = render(view());
  const badge = () => document.querySelector("#domain-products .m6-card-head .ant-tag")!;
  expect(badge().textContent).toBe("P0 风险");
  expect(document.querySelector("#domain-aftersales .m6-card-head .ant-tag")!.textContent).toBe(
    "观察",
  );
  for (const level of ["P1", "P2"] as const) {
    data[1].data!.observations = [{ ...obs, sourcePriority: level }];
    data[2].data!.observations = [{ ...obs, domain: "promotion", sourcePriority: level }];
    rerender(view());
    expect(badge().textContent).toBe(`${level} 风险`);
    expect(document.querySelector("#domain-promotion .m6-card-head .ant-tag")!.textContent).toBe(
      `${level} 风险`,
    );
  }
  data[1].data!.observations = [];
  rerender(view());
  expect(badge().textContent).toBe("观察");
});

it("首次域读取失败明确失败而非暂无数据，保留值带原日期及失败说明", () => {
  const data = states();
  data[1].data = undefined;
  data[1].error = "数据读取超时，请稍后重试。";
  data[2].retained = true;
  data[2].error = "服务暂时繁忙，请稍后重试。";
  data[2].data!.card.value = "2.5";
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const failed = document.querySelector("#domain-products")!;
  expect(failed.textContent).toContain("读取失败");
  expect(failed.textContent).not.toContain("暂无数据");
  const retained = document.querySelector("#domain-promotion")!;
  expect(retained.textContent).toContain("当前显示上次可用数据 · 2026-10-27");
  expect(retained.textContent).toContain("服务暂时繁忙");
});

it("诊断失败的动作空态明确读取失败，不把未读取建议写成没有待办", () => {
  const data = states();
  for (const state of data) {
    state.data!.observations = [];
    state.data!.actions = [];
    state.data!.observationState = "not_available";
  }
  data[1].error = "风险与建议读取失败：服务暂时繁忙";
  data[1].observationError = "服务暂时繁忙";
  vi.mocked(useOverview).mockReturnValue(data);
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
  const actions = document.querySelector("#actions")!;
  expect(actions.textContent).toContain("来源建议读取失败");
  expect(actions.textContent).not.toContain("首页关注对象暂无来源建议");
  expect(actions.textContent).toContain("不代表没有待处理事项");
});
