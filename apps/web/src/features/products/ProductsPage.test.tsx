// @vitest-environment jsdom
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProductsPage } from "./ProductsPage";

vi.mock("../../shared/layouts/DashboardLayout", () => ({
  DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: () => null,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
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
function publicationResponse() {
  return new Response(
    JSON.stringify({
      data: {
        products: "publication-1",
        promotion: "publication-1",
        sales: "publication-1",
        aftersales: "publication-1",
      },
    }),
  );
}
it("retains all six sections and available risk fields when upstream is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        url.includes("/published-versions")
          ? publicationResponse()
          : new Response(JSON.stringify({ error: { code: "PRODUCTS_NOT_CONNECTED" } }), {
              status: 503,
            }),
      ),
    ),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter>
        <ProductsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("商品数据尚未接通", {}, { timeout: 4000 })).toBeTruthy();
  expect(
    vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/products/meta")),
  ).toHaveLength(3);
  for (const id of [
    "product-brief",
    "product-scores",
    "overview",
    "product-diagnosis",
    "risk",
    "product-days",
  ])
    expect(document.getElementById(id)).toBeTruthy();
  expect(screen.getAllByText("估算影响金额")[0]).toBeTruthy();
  expect(screen.queryByText("截止时间")).toBeNull();
  expect(screen.getAllByText("交易金额同比").length).toBeGreaterThan(0);
  expect(screen.queryByText("¥0.00")).toBeNull();
});

it("keeps the business cutoff concise and explains the later sync time in details", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        url.includes("/published-versions")
          ? publicationResponse()
          : new Response(
              JSON.stringify(
                url.includes("/meta")
                  ? {
                      data: {
                        data_version: "header-test",
                        date_from: "2026-08-30",
                        date_to: "2026-10-28",
                        observed_rows: "122414",
                        product_count: "2816",
                        success_at: "2026-10-29T10:13:29+08:00",
                        complete_through: null,
                        completeness: "unknown",
                        profile_basis: "按当前资料归属",
                        latest_attempt: { status: "succeeded" },
                        sources: [],
                      },
                      meta: { data_version: "header-test", request_id: "test" },
                    }
                  : { error: { code: "PRODUCTS_NOT_CONNECTED" } },
              ),
              { status: url.includes("/meta") ? 200 : 503 },
            ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ProductsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const cutoff = await screen.findByText("数据截止：2026-10-28");
  expect(cutoff.textContent).not.toMatch(/未知|成功|10\/29/);
  fireEvent.focus(cutoff);
  expect(await screen.findByText(/最近同步成功：10\/29 10:13/)).toBeTruthy();
  expect(screen.getByText(/同步时间不代表业务数据日期/)).toBeTruthy();
  expect(screen.getByText(/尚未取得上游每日齐数的确认依据/)).toBeTruthy();
  expect(screen.getByText(/运营、品类、等级按当前商品资料分组/)).toBeTruthy();
});
