// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { useSalesData } from "./queries";
import { getSalesFilters, getSalesResult } from "./salesApi";
import { testFilters, testMeta, testMetrics, testShops } from "../testFixtures";

vi.mock("./salesApi", () => ({ getSalesFilters: vi.fn(), getSalesResult: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("updates same-version source status and clears recovered polling errors without fetching facts", async () => {
  const initial = {
    ...testFilters,
    data: {
      ...testFilters.data,
      sources: {
        shop_ratings: {
          state: "observed",
          observed_from: null,
          observed_through: "2026-10-26",
          source_updated_text: null,
          last_attempt_status: "succeeded",
          last_attempt_at: "2026-10-28T00:00:00Z",
          last_success_at: "2026-10-28T00:00:00Z",
        },
      },
    },
  };
  vi.mocked(getSalesFilters).mockResolvedValue(initial);
  vi.mocked(getSalesResult).mockResolvedValue({
    missingShopDays: 0,
    performance: testMetrics,
    shops: testShops,
    meta: testMeta,
  });
  const { result } = renderHook(() => useSalesData(), {
    wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter>,
  });
  await waitFor(() => expect(result.current.result).not.toBeNull());
  vi.mocked(getSalesFilters).mockRejectedValueOnce(new Error("offline"));
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await waitFor(() => expect(result.current.error).toContain("自动检查"));
  const failed = {
    ...initial,
    meta: { ...testMeta, last_attempt_status: "failed", last_attempt_at: "2026-10-28T02:00:00Z" },
    data: {
      ...initial.data,
      sources: {
        shop_ratings: {
          ...initial.data.sources.shop_ratings,
          last_attempt_status: "failed",
          last_attempt_at: "2026-10-28T02:00:00Z",
        },
      },
    },
  };
  vi.mocked(getSalesFilters).mockResolvedValue(failed);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await waitFor(() => expect(result.current.result?.meta.last_attempt_status).toBe("failed"));
  expect(result.current.filters?.data.sources?.shop_ratings.last_attempt_status).toBe("failed");
  expect(result.current.error).toBeNull();
  expect(getSalesResult).toHaveBeenCalledTimes(1);
});

function renderSales(entry = "/sales", initial = testFilters) {
  vi.mocked(getSalesFilters).mockResolvedValue({
    ...initial,
    data: {
      ...initial.data,
      shops: initial.data.shops.map((s) => ({
        ...s,
        source_shop_id: s.shop_id === "spigf" ? "663599807" : "799193231",
      })),
    },
  });
  vi.mocked(getSalesResult).mockResolvedValue({
    missingShopDays: 0,
    performance: testMetrics,
    shops: testShops,
    meta: testMeta,
  });
  return renderHook(
    () => ({ data: useSalesData(), navigate: useNavigate(), location: useLocation() }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
      ),
    },
  );
}

it("applies an explicit single payment day and source shop id before fetching facts", async () => {
  const { result } = renderSales(
    "/sales?month=2026-09&from=2026-09-15&to=2026-09-15&shop=799193231#performance",
  );
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  expect(result.current.data.scope).toEqual({
    month: "2026-09",
    from: "2026-09-15",
    to: "2026-09-15",
    shop: "spihw",
  });
  expect(getSalesResult).toHaveBeenLastCalledWith(
    expect.objectContaining({ from: "2026-09-15", to: "2026-09-15", shop: "spihw" }),
    "synthetic-v1",
    expect.any(AbortSignal),
  );
  expect(getSalesResult).toHaveBeenCalledTimes(1);
});

it.each([
  "month=2026-13",
  "month=2026-08",
  "from=2026-09-31&to=2026-10-01",
  "from=2026-09-15",
  "from=2026-09-20&to=2026-09-10",
  "from=2026-10-28&to=2026-10-28",
  "shop=missing",
  "month=2026-09&month=2026-10",
])("rejects invalid explicit scope without requesting fallback facts: %s", async (query) => {
  const { result } = renderSales(`/sales?${query}`);
  await waitFor(() => expect(result.current.data.error).toContain("链接范围"));
  expect(getSalesResult).not.toHaveBeenCalled();
  expect(result.current.data.result).toBeNull();
});

it("updates the URL on manual filters and restores filters through browser history", async () => {
  const { result } = renderSales(
    "/sales?month=2026-09&from=2026-09-15&to=2026-09-15&shop=spigf&source=hub#performance",
  );
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  act(() => result.current.data.changeScope({ shop: "spihw" }));
  await waitFor(() => expect(result.current.data.scope.shop).toBe("spihw"));
  expect(new URLSearchParams(result.current.location.search).get("shop")).toBe("spihw");
  expect(new URLSearchParams(result.current.location.search).get("source")).toBe("hub");
  expect(result.current.location.hash).toBe("#performance");
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  const calls = vi.mocked(getSalesResult).mock.calls.length;
  act(() => result.current.data.changeScope({ shop: "spihw" }));
  expect(getSalesResult).toHaveBeenCalledTimes(calls);
  act(() => result.current.navigate(-1));
  await waitFor(() => expect(result.current.data.scope.shop).toBe("spigf"));
  act(() => result.current.navigate(1));
  await waitFor(() => expect(result.current.data.scope.shop).toBe("spihw"));
});

it("aborts obsolete facts on URL navigation and ignores their late response", async () => {
  const { result } = renderSales();
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  let release = () => {};
  let previousSignal: AbortSignal | undefined;
  vi.mocked(getSalesResult).mockImplementationOnce(
    (_scope, _version, signal) =>
      new Promise((resolve) => {
        previousSignal = signal;
        release = () =>
          resolve({
            missingShopDays: 0,
            performance: { ...testMetrics, shop_revenue: "999" },
            shops: testShops,
            meta: testMeta,
          });
      }),
  );
  act(() => result.current.navigate("?from=2026-09-15&to=2026-09-15&shop=spigf"));
  await waitFor(() => expect(previousSignal).toBeDefined());
  act(() => result.current.navigate("?from=2026-09-16&to=2026-09-16&shop=spihw"));
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  expect(previousSignal?.aborted).toBe(true);
  await act(async () => release());
  expect(result.current.data.scope.to).toBe("2026-09-16");
  expect(result.current.data.result?.performance.shop_revenue).toBe("30.50");
});

it.each(["", "?shop=spigf", "?month=2026-09&from=2026-09-15&to=2026-09-15"])(
  "follows fresh defaults while preserving explicit calendar scope: %s",
  async (query) => {
    // 首次返回较早默认月；后续版本推进到10月，显式日期维持原选择。
    const { result } = renderSales(`/sales${query}`, {
      ...testFilters,
      data: {
        ...testFilters.data,
        default_month: "2026-09",
        recommended_date_from: "2026-09-01",
        recommended_date_to: "2026-09-30",
      },
    });
    await waitFor(() => expect(result.current.data.result).not.toBeNull());
    expect(result.current.data.scope.from).toBe(
      query.includes("from=") ? "2026-09-15" : "2026-09-01",
    );
    const next = { ...testFilters, meta: { ...testMeta, data_version: "synthetic-v2" } };
    vi.mocked(getSalesFilters).mockResolvedValue(next);
    await act(async () => window.dispatchEvent(new Event("focus")));
    await waitFor(() =>
      expect(result.current.data.filters?.meta.data_version).toBe("synthetic-v2"),
    );
    expect(result.current.data.scope.from).toBe(
      query.includes("from=") ? "2026-09-15" : "2026-10-01",
    );
    expect(result.current.data.scope.shop).toBe(query.includes("shop=") ? "spigf" : "");
  },
);

it("only clears an invalid URL after an explicit reset and preserves its anchor", async () => {
  const { result } = renderSales("/sales?month=bad&source=hub#performance");
  await waitFor(() => expect(result.current.data.error).toContain("链接范围"));
  expect(getSalesResult).not.toHaveBeenCalled();
  act(() => result.current.data.resetScope());
  await waitFor(() => expect(result.current.data.result).not.toBeNull());
  expect(result.current.location.search).toBe("?source=hub");
  expect(result.current.location.hash).toBe("#performance");
  expect(result.current.data.scope.month).toBe("2026-10");
});
