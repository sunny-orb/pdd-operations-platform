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
