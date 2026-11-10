// @vitest-environment jsdom
import { act, cleanup, render, waitFor, screen, fireEvent } from "@testing-library/react";
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
  vi.useRealTimers();
});

it("总览和明细的中文组词不提交筛选，结束并停顿后只提交完整词", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const change = vi.fn();
  const state = {
    version: "",
    metadata: {},
    change,
    reset: vi.fn(),
    stale: vi.fn(),
  } as unknown as ProductsState;
  const scope = readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-31");
  render(
    <QueryClientProvider client={client}>
      <Filters name="main" scope={scope} state={state} />
      <Filters name="detail" scope={scope} state={state} />
    </QueryClientProvider>,
  );
  for (const [label, name] of [
    ["总览搜索商品", "main"],
    ["明细搜索商品", "detail"],
  ]) {
    change.mockClear();
    const input = screen.getByRole("textbox", { name: label }) as HTMLInputElement;
    const chooser = screen.getByRole("combobox", {
      name: name === "main" ? "总览商品" : "明细商品",
    });
    input.focus();
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "shangpin" } });
    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(change).not.toHaveBeenCalled();
    expect(input.value).toBe("shangpin");
    expect(chooser.getAttribute("aria-expanded")).toBe("false");
    fireEvent.change(input, { target: { value: "商品" } });
    fireEvent.compositionEnd(input, { data: "商品" });
    await act(() => vi.advanceTimersByTimeAsync(499));
    expect(change).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(change).toHaveBeenCalledExactlyOnceWith(name, { keyword: "商品", page: 1 });
    expect(document.activeElement).toBe(input);
  }
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

it.each(["main", "detail"] as const)(
  "%s关键词在候选框回显但不自动选中，清空后恢复全部单款",
  (name) => {
    const client = new QueryClient();
    clients.push(client);
    const scope = {
      ...readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-31"),
      keyword: "小糖豆",
    };
    client.setQueryData(["m1", "catalog", "main", "v1", scope.from, scope.to], {
      shops: [{ value: "s1", label: "甲店" }],
      total: 1,
      items: [
        {
          value: "p1",
          label: "小糖豆帽 · 甲店 · 1001",
          search_text: "小糖豆帽 1001",
          shop: "s1",
          operator: null,
          category: null,
          level: null,
        },
      ],
    });
    const state = {
      version: "v1",
      metadata: { data: { date_from: scope.from, date_to: scope.to } },
      change: vi.fn(),
      reset: vi.fn(),
      stale: vi.fn(),
    } as unknown as ProductsState;
    const element = (keyword: string, products: string[] = []) => (
      <QueryClientProvider client={client}>
        <Filters name={name} scope={{ ...scope, keyword, products }} state={state} />
      </QueryClientProvider>
    );
    const view = render(element("小糖豆"));
    expect(screen.getByText("小糖豆 · 1个候选")).toBeTruthy();
    expect(state.change).not.toHaveBeenCalled();
    view.rerender(element("小糖豆", ["p1"]));
    // jsdom没有宽度，多选响应式标签会折叠；仍不得用候选占位冒充已选。
    expect(state.change).not.toHaveBeenCalled();
    expect(screen.queryByText("小糖豆 · 1个候选")).toBeNull();
    view.rerender(element(""));
    expect(screen.getByText("全部单款")).toBeTruthy();
    expect(state.change).not.toHaveBeenCalled();
  },
);
