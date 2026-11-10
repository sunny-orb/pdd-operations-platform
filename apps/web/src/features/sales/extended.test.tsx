// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));
import { cleanup, render as renderView, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { SalesMeta } from "./api/contracts";
import { SalesPage } from "./SalesPage";
import { syntheticResponse } from "./testFixtures";
function render(ui: Parameters<typeof renderView>[0]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderView(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("各来源接入后原位显示退款推广评分和地区，地区筛选独立于经营日期", async () => {
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const style = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((e) => style(e));
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      calls.push(path);
      const u = new URL(path, "http://localhost");
      let response: { data: Record<string, unknown>; meta: SalesMeta } = syntheticResponse(path);
      const source = {
        state: "observed",
        observed_from: "2026-08-24",
        observed_through: "2026-10-26",
        source_updated_text: null,
        last_success_at: null,
        last_attempt_status: "succeeded",
      };
      if (u.pathname.endsWith("/filters") && !path.includes("product-regions"))
        response = {
          ...response,
          data: {
            ...response.data,
            capabilities: { ratings: true, regions: true, product_regions: true },
            sources: { shop_ratings: source, shop_regions: source, product_regions: source },
          },
          meta: { ...response.meta, input_versions: { shop_daily: "synthetic" } },
        };
      if (u.pathname.endsWith("/performance"))
        response = {
          ...response,
          data: {
            ...response.data,
            promotion_spend: "1234",
            promotion_ctr: "0.1",
            refund_rate: "0.2",
            refund_match: {
              matched_refunds: "20",
              matched_sales: "100",
              matched_days: 1,
              sales_coverage: "1",
              excluded_days: 0,
              excluded_sales: "0",
              missing_dates: [],
              state: "same_day_reference",
            },
            promotion_channels: [],
          },
        };
      if (u.pathname.endsWith("/targets"))
        response = {
          data: {
            month_actual: "100",
            annual_actual: "1000",
            month_target: null,
            annual_target: null,
            month_ratio: null,
            annual_ratio: null,
            gap: null,
            state: "source_unavailable",
          },
          meta: response.meta,
        };
      if (u.pathname.endsWith("/ratings"))
        response = {
          data: {
            latest_date: "2026-10-26",
            latest: [
              {
                shop_id: "spigf",
                date: "2026-10-26",
                score: "98.76",
                raw_score: "0.9876",
                state: "observed",
                source_created_time_raw: null,
              },
            ],
            points: [],
            source,
          },
          meta: response.meta,
        };
      if (u.pathname.endsWith("/regions") || u.pathname.endsWith("/product-regions"))
        response = {
          data: {
            items: [],
            totals: {
              transaction_amount: "321",
              transaction_orders: "2",
              transaction_buyers: "1",
              actual_shipped_amount: "567",
              actual_shipped_qty: "3",
              actual_returned_amount: "2",
              actual_returned_qty: "1",
              refund_rate: "0.01",
            },
            region_count: 0,
            null_counts: {},
            missing_shop_days: [],
            source,
          },
          meta: response.meta,
        };
      if (u.pathname.endsWith("/product-regions/filters"))
        response = {
          data: { categories: ["雨具"], products: [], total: 0, offset: 0, limit: 100, source },
          meta: response.meta,
        };
      return Promise.resolve({ ok: true, json: async () => response });
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText("98.76 分")).toBeTruthy());
  expect(screen.getByTestId("sales-kpis").textContent).toContain("¥1,234");
  expect(screen.getByTestId("sales-kpis").textContent).toContain("20.00%");
  await waitFor(() => expect(screen.getByText("¥321")).toBeTruthy());
  const before = calls.filter((p) => p.includes("/performance")).length;
  fireEvent.change(screen.getByLabelText("查找产品名称或编码"), { target: { value: "遥远款号" } });
  await waitFor(() => expect(calls.some((p) => p.includes("search=%E9%81%A5"))).toBe(true));
  expect(calls.filter((p) => p.includes("/performance"))).toHaveLength(before);
  expect(document.querySelectorAll("details:not(.target-details)")).toHaveLength(6);
  expect(screen.getByText("查看地区交易明细与口径").closest("details")!.open).toBe(false);
  expect(screen.getByText("查看统计口径与历史补充").closest("details")!.open).toBe(false);
  expect(screen.queryByLabelText("推广数据提示")).toBeNull();
  expect(document.querySelector("#targets .target-details")?.hasAttribute("open")).toBe(false);
});
