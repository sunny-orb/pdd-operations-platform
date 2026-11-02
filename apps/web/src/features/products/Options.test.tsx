// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { useOptions, type ProductsState } from "./data";
import { readScope, type ScopeName } from "./scope";

const items = Array.from({ length: 151 }, (_, i) => ({
  value: `listing-${i}`,
  label: `商品${i} · 甲店 · ${1000 + i}`,
  shop: "s1",
  operator: i < 100 ? "甲" : "乙",
  category: i < 100 ? "帽子" : "衣服",
  level: i < 100 ? "A级" : "新品",
  search_text: `${1000 + i} 商品${i} 英文ABC 别名${i}`,
}));
const catalog = { items, shops: [{ value: "s1", label: "甲店" }], total: 151 };
const scope = readScope(new URLSearchParams(), "main", "2026-10-02", "2026-11-01");
const state = {
  version: "v1",
  metadata: { data: { date_from: "2026-09-03", date_to: "2026-11-01" } },
  stale: vi.fn(),
} as unknown as ProductsState;
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});
const reply = (data = catalog) => ({
  ok: true,
  json: async () => ({ data, meta: { data_version: "v1", request_id: "test" } }),
});
function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  });
  clients.push(client);
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

it("全部151个候选可直接选择，维度切换和候选搜索不再请求目录", async () => {
  const fetch = vi.fn(async () => reply());
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(
    ({ category, needle }) => useOptions({ ...scope, category }, state, needle, "main"),
    {
      initialProps: { category: "", needle: "" },
      wrapper: wrapper(),
    },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.products).toHaveLength(151);
  rerender({ category: "衣服", needle: "" });
  expect(result.current.data?.products).toHaveLength(51);
  expect(result.current.isFetching).toBe(false);
  rerender({ category: "衣服", needle: "别名150" });
  expect(result.current.data?.products.map((p) => p.value)).toEqual(["listing-150"]);
  rerender({ category: "", needle: "abc" });
  expect(result.current.data?.products).toHaveLength(151);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("级联只清除失效下级，搜索无匹配也保留已选商品及其名称", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => reply()),
  );
  const { result, rerender } = renderHook(
    ({ operator, needle }) =>
      useOptions(
        {
          ...scope,
          operator,
          category: "帽子",
          level: "A级",
          products: ["listing-0", "listing-150"],
        },
        state,
        needle,
        "main",
      ),
    {
      initialProps: { operator: "甲", needle: "" },
      wrapper: wrapper(),
    },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.selection).toEqual({
    operator: "甲",
    category: "帽子",
    level: "A级",
    products: ["listing-0"],
  });
  rerender({ operator: "甲", needle: "不存在" });
  expect(result.current.data?.products).toEqual([]);
  expect(result.current.data?.selection.products).toEqual(["listing-0"]);
  expect(
    result.current.data?.selected_products.find((p) => p.value === "listing-0")?.label,
  ).toContain("商品0");
  rerender({ operator: "乙", needle: "" });
  expect(result.current.data?.selection).toEqual({
    operator: "乙",
    category: "",
    level: "",
    products: ["listing-150"],
  });
});

it("总览和明细共享目录，新品、日期和版本分别隔离，失败不当成空选项", async () => {
  let finish: ((value: unknown) => void) | undefined;
  const fetch = vi.fn().mockResolvedValue(reply());
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(
    ({ name, version, from }: { name: ScopeName; version: string; from: string }) =>
      useOptions({ ...scope, from }, { ...state, version }, "", name),
    {
      initialProps: { name: "main" as ScopeName, version: "v1", from: scope.from },
      wrapper: wrapper(),
    },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  rerender({ name: "detail", version: "v1", from: scope.from });
  expect(result.current.isFetching).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ name: "score", version: "v1", from: scope.from });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  rerender({ name: "main", version: "v1", from: "2026-10-03" });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  fetch.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  rerender({ name: "main", version: "v2", from: scope.from });
  expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
  await act(async () =>
    finish?.({ ok: false, status: 500, json: async () => ({ error: { code: "REQUEST_FAILED" } }) }),
  );
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toBeUndefined();
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() => expect(result.current.data?.products).toHaveLength(151));
});
