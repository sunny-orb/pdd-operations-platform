// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { OverviewPage } from "./OverviewPage";
import { useOverview } from "./useOverview";
import { actionFromObservation, productLink, type Observation } from "./model";
import type { DomainState } from "./data";
import { useWeather } from "./weather";
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
afterEach(cleanup);
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
        title: ["店铺销售 GMV", "商品经营", "推广效率", "售后退款"][i],
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
  expect(screen.getByText("观察异常对象数")).toBeTruthy();
  expect(screen.getByText("已接范围", { exact: true })).toBeTruthy();
  const table = screen.getByRole("table");
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((x) => x.textContent),
  ).toEqual(["问题", "影响金额", "原因假设", "动作", "负责人", "截止时间", "当前进度", "验证指标"]);
  expect(within(table).getByText("未分配")).toBeTruthy();
  expect(within(table).getByText("建议，未登记")).toBeTruthy();
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
  expect(within(alerts).getByText("源域 P0")).toBeTruthy();
  fireEvent.click(within(alerts).getByText("P0", { exact: true }));
  expect(within(alerts).queryByText("单款观察提示")).toBeNull();
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
  expect(screen.getByText("天气部分字段待核")).toBeTruthy();
  expect(screen.getByText(/SOURCE_TIMEOUT/)).toBeTruthy();
});
