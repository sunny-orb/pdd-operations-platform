// @vitest-environment jsdom
import { cleanup, render, waitFor, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Filters } from "./Filters";
import type { ProductsState } from "./data";
import { readScope } from "./scope";

const clients: QueryClient[] = [];
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
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
  clients.forEach((c) => c.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

it("新品目录为空时不清除固定新品条件，不反复重置URL", async () => {
  const fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({
      data: { items: [], shops: [], total: 0 },
      meta: { data_version: "v1", request_id: "test" },
    }),
  }));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const state = {
    version: "v1",
    metadata: { data: { date_from: "2026-10-01", date_to: "2026-10-31" } },
    change: vi.fn(),
    reset: vi.fn(),
    stale: vi.fn(),
  } as unknown as ProductsState;
  const scope = {
    ...readScope(new URLSearchParams(), "score", "2026-10-01", "2026-10-31"),
    level: "新品",
  };
  render(
    <QueryClientProvider client={client}>
      <Filters name="score" scope={scope} state={state} />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "评分品类" }).hasAttribute("disabled")).toBe(false),
  );
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "评分品类" }));
  await screen.findByText("暂无匹配选项");
  expect(state.change).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("多等级选项合并显示，商品候选仍列出实际成员", async () => {
  const items = ["A级/B级混合", "B级/清仓混合"].map((level, i) => ({
    value: `l${i}`,
    label: `商品${i}`,
    search_text: `商品${i}`,
    shop: "s1",
    operator: "甲",
    category: "帽子",
    level,
    level_group: "多等级",
    level_details: i === 0 ? ["A级", "B级"] : ["B级", "清仓"],
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        data: { items, shops: [{ value: "s1", label: "甲店" }], total: 2 },
        meta: { data_version: "v1", request_id: "test" },
      }),
    })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const state = {
    version: "v1",
    metadata: { data: { date_from: "2026-10-01", date_to: "2026-10-31" } },
    change: vi.fn(),
    reset: vi.fn(),
    stale: vi.fn(),
  } as unknown as ProductsState;
  const scope = {
    ...readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-31"),
    level: "多等级",
  };
  render(
    <QueryClientProvider client={client}>
      <Filters name="main" scope={scope} state={state} />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "总览商品" }).hasAttribute("disabled")).toBe(false),
  );
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "总览商品" }));
  expect(await screen.findByText("多等级（含A级、B级）")).toBeTruthy();
  expect(screen.getByText("多等级（含B级、清仓）")).toBeTruthy();
  expect(state.change).not.toHaveBeenCalled();
});
