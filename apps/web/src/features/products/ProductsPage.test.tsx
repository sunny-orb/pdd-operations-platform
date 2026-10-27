// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
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
it("retains all six sections and original table fields when upstream is unavailable", async () => {
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "PRODUCTS_NOT_CONNECTED" } }), {
        status: 503,
      }),
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
  expect(await screen.findByText("商品经营数据尚未接通")).toBeTruthy();
  for (const id of [
    "product-brief",
    "product-scores",
    "overview",
    "product-diagnosis",
    "risk",
    "product-days",
  ])
    expect(document.getElementById(id)).toBeTruthy();
  expect(screen.getAllByText("预计损失")[0]).toBeTruthy();
  expect(screen.getAllByText("截止时间")[0]).toBeTruthy();
  expect(screen.getAllByText("交易金额同比").length).toBeGreaterThan(0);
  expect(screen.queryByText("¥0.00")).toBeNull();
});
