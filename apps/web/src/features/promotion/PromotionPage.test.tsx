// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PromotionPage } from "./PromotionPage";
vi.mock("./Trend", () => ({ Trend: () => <div>趋势图</div> }));
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const metric = (value: string | null, label: string, type = "currency") => ({
  value,
  label,
  type,
  reason: value == null ? "来源缺失" : null,
  status: value == null ? "unavailable" : "available",
  change: null,
});
const metrics = Object.fromEntries(
  ["spend", "revenue", "roi", "impressions", "clicks", "ctr", "cvr", "cpc"].map((key) => [
    key,
    metric("12.12", key),
  ]),
);
function mount() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const mode = url.includes("mode=product") ? "product" : "store";
      const data = url.includes("/meta?")
        ? {
            mode,
            date_from: "2026-08-01",
            date_to: "2026-10-27",
            recommended_from: "2026-09-28",
            recommended_to: "2026-10-27",
            observed_rows: "20",
            complete_through: null,
            completeness: "unknown",
            sources: [],
          }
        : url.includes("/options?")
          ? {
              shops: [],
              channels: [],
              products: [],
              selected_products: [],
              categories: [],
              levels: [],
              operators: [],
              total: 0,
            }
          : url.includes("/days?")
            ? { items: [], total: 0, page: 1, size: 25, total_pages: 1, row_grain: "day" }
            : url.includes("/diagnosis?")
              ? {
                  current_window: {},
                  previous_window: {},
                  overall: null,
                  products: [],
                  flagged_products: [],
                  focused_products: [],
                  shops: [],
                  categories: [],
                  counts: {},
                  coverage: {
                    stale: true,
                    observed_through: "2026-10-20",
                    covered_days: 12,
                    missing_dates: [],
                  },
                  rule_version: "SOP v1.4",
                  rule_states: {},
                  status: "insufficient",
                }
              : {
                  metrics,
                  net: {
                    net_revenue: metric(null, "净交易额"),
                    net_roi: metric(null, "净投产比", "ratio"),
                  },
                  series: [],
                  funnel: [],
                  top5: [],
                  coverage: {
                    missing_count: 1,
                    missing: [
                      {
                        date: "2026-10-27",
                        shop_name: "测试店",
                        channel_label: "明星店铺",
                        reason: "暂无记录",
                      },
                    ],
                    completeness: "unknown",
                    complete_through: null,
                  },
                  current_record_count: "20",
                  dataset_record_count: "20",
                  windows: [],
                  channel_summaries: [],
                };
      return {
        ok: true,
        json: async () => ({ data, meta: { data_version: "v1", request_id: "test" } }),
      };
    }),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <PromotionPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
it("默认店铺模式保留八指标、净额缺失、缺日和十列表格；指标不能全部取消", async () => {
  mount();
  await screen.findByText("拼多多店铺推广看板");
  const directory = screen.getByRole("navigation", { name: "本页目录" });
  expect(within(directory).getByRole("link", { name: "筛选范围" }).getAttribute("href")).toBe(
    "#promotion-filters",
  );
  expect(within(directory).getByRole("link", { name: "推广指标" }).getAttribute("href")).toBe(
    "#promotion-metrics",
  );
  expect(screen.getByRole("banner", { name: "看板导航" })).toBeTruthy();
  expect(document.getElementById("promotion-filters")).toBeTruthy();
  await screen.findByText(/存在 1 个店铺渠道日缺失/);
  expect(screen.getByText(/净交易额：—/)).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "来源" })).toBeTruthy();
  const spend = screen.getByRole("button", { name: /^总花费/ });
  const revenue = screen.getByRole("button", { name: /^交易额/ });
  await userEvent.click(spend);
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(screen.getByText(/查看缺日与历史覆盖提示/));
  expect(screen.getByText(/测试店 · 明星店铺/)).toBeTruthy();
});
it("商品模式包含漏斗、日期参考、SOP、四层清单及浏览进度；共享导航保留", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "商品推广明细" }));
  await screen.findByText("拼多多推广数据看板");
  const directory = screen.getByRole("navigation", { name: "本页目录" });
  expect(within(directory).getByRole("link", { name: "SOP经营诊断" }).getAttribute("href")).toBe(
    "#promotion-diagnosis",
  );
  expect(within(directory).getByRole("link", { name: "每日推广明细" }).getAttribute("href")).toBe(
    "#promotion-days",
  );
  await screen.findByText("从曝光到成交");
  expect(screen.getByText("低投产日期 Top 5")).toBeTruthy();
  expect(screen.getByText("从推广结果定位到商品与行动")).toBeTruthy();
  expect(screen.getByText(/全部商品扫描/)).toBeTruthy();
  expect(screen.getByRole("slider", { name: "推广明细上下浏览进度" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /返回运营看板中心/ })).toBeTruthy();
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", {
        name: /^(总花费|交易额|实际投产比|曝光量|点击量|点击率|点击转化率|CPC)/,
      }),
    ).toHaveLength(8),
  );
});

it("诊断读取coverage内陈旧状态与14日覆盖，不遗漏历史复核提示", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "商品推广明细" }));
  await screen.findByText(/源数据观察截至 2026-10-20，尚未更新至昨日/);
  await userEvent.click(screen.getByText(/全部商品扫描/));
  expect(screen.getByText(/两个周期有记录日期 12\/14/)).toBeTruthy();
});
