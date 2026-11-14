// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
// 发布信号本身在shared及overview组合测试验证，本文件只控制领域响应顺序。
vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));
import { z } from "zod";
import { useOptions, useProducts, useResource, type ProductsState } from "./data";
import { readScope } from "./scope";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each(["main_from", "main_to", "detail_from", "detail_to"])(
  "%s单日链接提交关键词不会被误判为日期重置",
  async (key) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    const name = key.startsWith("main") ? "main" : "detail";
    const { result } = renderHook(() => useProducts(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[`/?${key}=2026-11-10`]}>{children}</MemoryRouter>
        </QueryClientProvider>
      ),
    });
    const before = result.current.dateSelectionKey(name);
    await act(() => result.current.change(name, { keyword: "糖豆" }));
    expect(result.current.scope(name).keyword).toBe("糖豆");
    expect(result.current.scope(name).from).toBe("2026-11-10");
    expect(result.current.scope(name).to).toBe("2026-11-10");
    expect(result.current.dateSelectionKey(name)).toBe(before);
    await act(() => result.current.change(name, { from: "2026-11-01" }));
    expect(result.current.dateSelectionKey(name)).not.toBe(before);
    client.clear();
  },
);

it("评分仅翻页时保留上一页布局，响应后替换；失败不冒用旧页结果", async () => {
  const replies: ((response: unknown) => void)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise((resolve) => replies.push(resolve))),
  );
  const respond = async (index: number, page: number, ok = true) => {
    await act(async () => {
      replies[index]({
        ok,
        status: ok ? 200 : 500,
        json: async () =>
          ok
            ? { data: { page }, meta: { data_version: "v1", request_id: "test" } }
            : { error: { code: "REQUEST_FAILED" } },
      });
    });
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const scope = readScope(new URLSearchParams(), "score", "2026-10-01", "2026-10-31");
  const state = {
    version: "v1",
    metadata: { data: { date_from: "2026-09-01", date_to: "2026-10-31" } },
    stale: vi.fn(),
  } as unknown as ProductsState;
  const schema = z.object({ page: z.number() });
  const { result, rerender } = renderHook(
    ({ page }) => ({ ...useResource("scores", schema, { ...scope, page }, state) }),
    {
      initialProps: { page: 1 },
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  await waitFor(() => expect(replies).toHaveLength(1));
  await respond(0, 1);
  await waitFor(() => {
    expect(result.current.error).toBeNull();
    expect(result.current.data?.page).toBe(1);
  });
  rerender({ page: 2 });
  expect(result.current.data?.page).toBe(1);
  expect(result.current.isPlaceholderData).toBe(true);
  await waitFor(() => expect(replies).toHaveLength(2));
  await respond(1, 2);
  await waitFor(() => expect(result.current.data?.page).toBe(2));
  expect(result.current.isPlaceholderData).toBe(false);
  rerender({ page: 3 });
  expect(result.current.data?.page).toBe(2);
  await waitFor(() => expect(replies).toHaveLength(3));
  await respond(2, 3, false);
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toBeUndefined();
  client.clear();
});

it.each(["filter", "version", "path"])("评分切换%s时不沿用其他范围的旧页", async (change) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const scope = readScope(new URLSearchParams(), "score", "2026-10-01", "2026-10-31");
  client.setQueryData(["m1", "scores", "v1", scope], { page: 1 });
  const state = {
    version: "v1",
    metadata: { data: { date_from: "2026-09-01", date_to: "2026-10-31" } },
    stale: vi.fn(),
  } as unknown as ProductsState;
  const { result, rerender } = renderHook(
    ({ path, version, category }) =>
      useResource(
        path,
        z.object({ page: z.number() }),
        { ...scope, category },
        { ...state, version },
      ),
    {
      initialProps: { path: "scores", version: "v1", category: "" },
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  expect(result.current.data?.page).toBe(1);
  rerender({
    path: change === "path" ? "days" : "scores",
    version: change === "version" ? "v2" : "v1",
    category: change === "filter" ? "帽子" : "",
  });
  expect(result.current.data).toBeUndefined();
  expect(result.current.isPlaceholderData).toBe(false);
  client.clear();
});

it("derives 31 days from each published cutoff and advances with a new version", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const seed = (end: string) =>
    client.setQueryData(["m1", "meta"], {
      data_version: end,
      date_from: "2026-08-30",
      date_to: end,
    });
  seed("2026-10-28");
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useProducts(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  });
  for (const name of ["main", "detail"] as const) {
    expect(result.current.scope(name).from).toBe("2026-09-28");
    expect(result.current.scope(name).to).toBe("2026-10-28");
  }
  seed("2026-10-29");
  await waitFor(() => expect(result.current.scope("main").to).toBe("2026-10-29"));
  expect(result.current.scope("main").from).toBe("2026-09-29");
  expect(result.current.scope("detail").from).toBe("2026-09-29");
  expect(fetch).not.toHaveBeenCalled();
  client.clear();
});

it("blocks reversed date requests, including manual retry, while retaining input", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const scope = readScope(
    new URLSearchParams("main_from=2026-10-20&main_to=2026-10-10"),
    "main",
    "2026-10-01",
    "2026-10-26",
  );
  const state = {
    version: "v",
    metadata: { data: { date_from: "2026-08-28", date_to: "2026-10-26" } },
    stale: vi.fn(),
  } as unknown as ProductsState;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useResource("overview", z.unknown(), scope, state), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  expect(result.current.error?.message).toContain("结束日期");
  expect(result.current.data).toBeUndefined();
  await result.current.refetch();
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
  expect(scope.from).toBe("2026-10-20");
});

it("总览和明细共享完整目录，翻页或独立筛选不再重查目录", async () => {
  const fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({
      data: {
        shops: [],
        total: 2,
        items: ["帽子", "衣服"].map((category, index) => ({
          value: String(index),
          label: category,
          shop: "s",
          operator: null,
          category,
          level: null,
          search_text: category,
        })),
      },
      meta: { data_version: "v1", request_id: "test" },
    }),
  }));
  vi.stubGlobal("fetch", fetch);
  const main = readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-27");
  const state = {
    version: "v1",
    metadata: { data: { date_from: "2026-08-28", date_to: "2026-10-27" } },
    stale: vi.fn(),
  } as unknown as ProductsState;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, rerender } = renderHook(
    ({ detail }) => ({
      main: useOptions(main, state, "", "main"),
      detail: useOptions(detail, state, "", "detail"),
    }),
    {
      initialProps: { detail: main },
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  await waitFor(() =>
    expect(result.current.main.isSuccess && result.current.detail.isSuccess).toBe(true),
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ detail: { ...main, page: 2, size: 50 } });
  expect(result.current.detail.isSuccess).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ detail: { ...main, category: "帽子" } });
  expect(result.current.main.data?.total).toBe(2);
  expect(result.current.detail.data?.total).toBe(1);
  expect(result.current.detail.isFetching).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  client.clear();
});

it.each(["overview", "scores", "days"])(
  "%s短时间切回已看条件不重复请求，新版本不复用旧结果",
  async (path) => {
    const fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        data: { value: "100" },
        meta: { data_version: "v1", request_id: "test" },
      }),
    }));
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } },
    });
    const scope = readScope(new URLSearchParams(), "main", "2026-10-02", "2026-11-01");
    const state = {
      version: "v1",
      metadata: { data: { date_from: "2026-09-01", date_to: "2026-11-01" } },
      stale: vi.fn(),
    } as unknown as ProductsState;
    const { result, rerender } = renderHook(
      ({ category, version }) =>
        useResource(
          path,
          z.object({ value: z.string() }),
          { ...scope, category },
          { ...state, version },
        ),
      {
        initialProps: { category: "帽子", version: "v1" },
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    rerender({ category: "书包", version: "v1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetch).toHaveBeenCalledTimes(2);
    rerender({ category: "帽子", version: "v1" });
    expect(result.current.data?.value).toBe("100");
    expect(result.current.isFetching).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(2);
    rerender({ category: "帽子", version: "v2" });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    client.clear();
  },
);
