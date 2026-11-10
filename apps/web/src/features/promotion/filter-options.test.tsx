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
import { usePromotion } from "./data";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
type ResponseLike = { ok: boolean; status: number; json: () => Promise<unknown> };
const products = Array.from({ length: 125 }, (_, i) => ({
  value: `listing-${i}`,
  listing_id: `listing-${i}`,
  label: `${100000 + i} · 商品${i} · 测试店铺`,
  product_id: String(100000 + i),
  product_name: `商品${i}`,
  shop_id: i < 120 ? "shop-a" : "shop-b",
  category: i < 120 ? "雨衣" : "童装",
  level: "A级",
  operator: i < 120 ? "小王" : "小李",
  search_names: i === 124 ? ["别名帽子"] : [],
}));
function setup({
  optionsReply,
  selectedReply,
  getVersion = () => "v1",
  entry = "/promotion?mode=product",
}: {
  optionsReply?: (query: URLSearchParams) => Promise<ResponseLike | undefined>;
  selectedReply?: (query: URLSearchParams, signal?: AbortSignal) => Promise<ResponseLike>;
  getVersion?: () => string;
  entry?: string;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const fetch = vi.fn(async (url: string, init?: { signal: AbortSignal }) => {
    const query = new URL(url, "http://localhost").searchParams;
    if (url.includes("/options/selected?") && selectedReply)
      return selectedReply(query, init?.signal);
    if (url.includes("/options?") && optionsReply) {
      const response = await optionsReply(query);
      if (response) return response;
    }
    const data = url.includes("/meta?")
      ? {
          mode: "product",
          date_from: "2026-10-01",
          date_to: "2026-10-31",
          recommended_from: "2026-10-02",
          recommended_to: "2026-10-31",
          observed_rows: "125",
          complete_through: null,
          completeness: "unknown",
          sources: [],
        }
      : url.includes("/options?")
        ? {
            shops: [
              { value: "shop-a", label: "甲店" },
              { value: "shop-b", label: "乙店" },
            ],
            channels: [],
            products: query.get("catalog") === "true" ? products : products.slice(0, 100),
            selected_products: [],
            categories: ["雨衣", "童装"],
            levels: ["A级"],
            operators: ["小王", "小李"],
            total: 125,
          }
        : {};
    return {
      ok: true,
      status: 200,
      json: async () => ({ data, meta: { data_version: getVersion(), request_id: "test" } }),
    };
  });
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => usePromotion(), {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={[entry]}>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </MemoryRouter>
    ),
  });
  return { ...hook, fetch };
}

const selectedResponse = (label: string, version = "v1"): ResponseLike => ({
  ok: true,
  status: 200,
  json: async () => ({
    data: {
      products: [
        { ...products[0], value: "listing-outside", listing_id: "listing-outside", label },
      ],
    },
    meta: { data_version: version, request_id: "selected" },
  }),
});

it("首次URL中的目录外商品仅轻量补名称，保留精确条件且不扩大日期目录", async () => {
  const { result, fetch } = setup({
    entry:
      "/promotion?mode=product&product_products=listing-outside&product_category=雨衣&product_level=A级%2FB级混合",
    selectedReply: async () => selectedResponse("历史商品 · 测试店铺"),
  });
  await waitFor(() =>
    expect(result.current.selectedProducts[0]?.label).toBe("历史商品 · 测试店铺"),
  );
  expect(result.current.scope).toMatchObject({
    products: ["listing-outside"],
    category: "雨衣",
    level: "A级/B级混合",
  });
  expect(result.current.options.data?.data.total).toBe(125);
  expect(
    result.current.options.data?.data.products.some((row) => row.value === "listing-outside"),
  ).toBe(false);
  const selectedCalls = () =>
    fetch.mock.calls.filter(([url]) => url.includes("/options/selected?"));
  expect(selectedCalls()).toHaveLength(1);
  const query = new URL(selectedCalls()[0][0], "http://localhost").searchParams;
  expect([...query.keys()].sort()).toEqual(["data_version", "mode", "selected_ids"]);
  expect(query.getAll("selected_ids")).toEqual(["listing-outside"]);
  expect(query.get("data_version")).toBe("v1");
  act(() => result.current.change({ products: ["listing-1"] }));
  act(() => result.current.change({ products: ["listing-outside"] }));
  expect(result.current.selectedProducts[0]?.label).toBe("历史商品 · 测试店铺");
  expect(selectedCalls()).toHaveLength(1);
  expect(fetch.mock.calls.filter(([url]) => url.includes("/options?"))).toHaveLength(1);
  expect(fetch.mock.calls.every(([url]) => url.startsWith("/api/v1/promotion/"))).toBe(true);
});

it("旧版本晚到的商品名称不会覆盖新版本，补名称请求随版本取消", async () => {
  let version = "v1";
  let release!: (response: ResponseLike) => void;
  let oldSignal: AbortSignal | undefined;
  const pending = new Promise<ResponseLike>((resolve) => {
    release = resolve;
  });
  const { result } = setup({
    entry: "/promotion?mode=product&product_products=listing-outside",
    getVersion: () => version,
    selectedReply: async (query, signal) => {
      if (query.get("data_version") === "v1") {
        oldSignal = signal;
        return pending;
      }
      return selectedResponse("新版名称", "v2");
    },
  });
  await waitFor(() => expect(oldSignal).toBeTruthy());
  version = "v2";
  await act(async () => {
    await result.current.meta.refetch();
  });
  await waitFor(() => expect(result.current.selectedProducts[0]?.label).toBe("新版名称"));
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => release(selectedResponse("旧版名称")));
  expect(result.current.selectedProducts[0]?.label).toBe("新版名称");
  expect(result.current.scope.products).toEqual(["listing-outside"]);
});

it("补名称失败不清空商品或目录，可单独重试且不重复读取目录", async () => {
  let fail = true;
  const { result, fetch } = setup({
    entry: "/promotion?mode=product&product_products=listing-outside",
    selectedReply: async () =>
      fail
        ? { ok: false, status: 503, json: async () => ({ error: { code: "UNAVAILABLE" } }) }
        : selectedResponse("恢复名称"),
  });
  await waitFor(() => expect(result.current.selectedOptions.error).toBeTruthy(), { timeout: 4000 });
  expect(result.current.scope.products).toEqual(["listing-outside"]);
  expect(result.current.options.data?.data.total).toBe(125);
  fail = false;
  await act(async () => {
    await result.current.selectedOptions.refetch();
  });
  await waitFor(() => expect(result.current.selectedProducts[0]?.label).toBe("恢复名称"));
  expect(fetch.mock.calls.filter(([url]) => url.includes("/options?"))).toHaveLength(1);
  expect(fetch.mock.calls.filter(([url]) => url.includes("/options/selected?"))).toHaveLength(4);
});

it("完整日期目录只取一次，切换条件保留全部选项，搜索只缩小商品候选", async () => {
  const { result, fetch } = setup();
  await waitFor(() => expect(result.current.options.data?.data.products).toHaveLength(125));
  const optionsCalls = () => fetch.mock.calls.filter(([url]) => url.includes("/options?"));
  expect(optionsCalls()).toHaveLength(1);
  act(() => result.current.setNeedle("别名帽子"));
  expect(result.current.options.data?.data.products.map((p) => p.value)).toEqual(["listing-124"]);
  // 搜索草稿只影响商品候选，不把其他下拉目录一并缩小。
  expect(result.current.options.data?.data.shops).toHaveLength(2);
  act(() => result.current.setNeedle(""));
  act(() => result.current.change({ shop: "shop-b" }));
  expect(result.current.options.data?.data.products).toHaveLength(125);
  expect(result.current.options.data?.data.categories).toEqual(["童装", "雨衣"]);
  expect(result.current.options.data?.data.operators).toEqual(["小李", "小王"]);
  act(() => result.current.change({ shop: "" }));
  expect(result.current.options.data?.data.products).toHaveLength(125);
  expect(optionsCalls()).toHaveLength(1);
});

it("首次进入先加载筛选目录，经营查询不抢占选项请求，目录失败也不阻断指标", async () => {
  let release!: (value: ResponseLike) => void;
  const pending = new Promise<ResponseLike>((resolve) => {
    release = resolve;
  });
  const { result, fetch } = setup({ optionsReply: async () => pending });
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.includes("/options?"))).toBe(true),
  );
  expect(
    fetch.mock.calls.some(([url]) => url.includes("/overview?") || url.includes("/days?")),
  ).toBe(false);
  await act(async () =>
    release({ ok: false, status: 503, json: async () => ({ error: { code: "UNAVAILABLE" } }) }),
  );
  await waitFor(() => expect(result.current.options.error).toBeTruthy(), { timeout: 4000 });
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.includes("/overview?"))).toBe(true),
  );
  expect(fetch.mock.calls.some(([url]) => url.includes("/days?"))).toBe(true);
});

it("每次选择只更新主动修改项，冲突条件和完整商品身份始终保留", async () => {
  const { result } = setup();
  await waitFor(() => expect(result.current.options.data).toBeTruthy());
  act(() =>
    result.current.change({ shop: "shop-a", category: "雨衣", level: "A级", operator: "小王" }),
  );
  act(() => result.current.change({ shop: "shop-b" }));
  expect(result.current.scope).toMatchObject({
    shop: "shop-b",
    category: "雨衣",
    operator: "小王",
    level: "A级",
  });
  act(() => result.current.change({ products: ["listing-124"] }));
  act(() => result.current.change({ category: "童装" }));
  expect(result.current.scope.products).toEqual(["listing-124"]);
  act(() => result.current.change({ products: [] }));
  expect(result.current.options.data?.data.products).toHaveLength(125);
});

it("已选商品不在新日期目录时仍保留身份和名称，日期变化不清空其他条件", async () => {
  const { result } = setup({
    optionsReply: async (query) =>
      query.get("date_from") === "2026-10-01"
        ? {
            ok: true,
            status: 200,
            json: async () => ({
              data: {
                shops: [],
                channels: [],
                products: [],
                selected_products: [],
                categories: [],
                levels: [],
                operators: [],
                total: 0,
              },
              meta: { data_version: "v1", request_id: "empty-date" },
            }),
          }
        : undefined,
  });
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(125));
  act(() => result.current.change({ products: ["listing-124"], category: "雨衣", page: 3 }));
  act(() => result.current.change({ from: "2026-10-01" }));
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(0));
  expect(result.current.scope).toMatchObject({
    products: ["listing-124"],
    category: "雨衣",
    page: 1,
  });
  expect(result.current.options.data?.data.selected_products[0]?.label).toBe(
    "100124 · 商品124 · 测试店铺",
  );
});

it("新版本目录尚未到达或不再包含已选商品时，名称和已选条件仍保留", async () => {
  let version = "v1";
  let release!: (value: ResponseLike) => void;
  const pending = new Promise<ResponseLike>((resolve) => {
    release = resolve;
  });
  const { result } = setup({
    getVersion: () => version,
    optionsReply: async (query) => (query.get("data_version") === "v2" ? pending : undefined),
  });
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(125));
  act(() => result.current.change({ products: ["listing-124"], shop: "shop-b", category: "童装" }));
  version = "v2";
  await act(async () => {
    await result.current.meta.refetch();
  });
  await waitFor(() => expect(result.current.version).toBe("v2"));
  expect(result.current.options.data).toBeUndefined();
  expect(result.current.selectedProducts[0]?.label).toBe("100124 · 商品124 · 测试店铺");
  expect(result.current.shopOptions.find((shop) => shop.value === "shop-b")?.label).toBe("乙店");
  await act(async () =>
    release({
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          shops: [],
          channels: [],
          products: [],
          selected_products: [],
          categories: [],
          levels: [],
          operators: [],
          total: 0,
        },
        meta: { data_version: "v2", request_id: "empty-new-version" },
      }),
    }),
  );
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(0));
  expect(result.current.options.data?.data.selected_products[0]?.label).toBe(
    "100124 · 商品124 · 测试店铺",
  );
  expect(result.current.scope).toMatchObject({
    products: ["listing-124"],
    shop: "shop-b",
    category: "童装",
  });
});

it("日期改变重新读取目录，回到已看日期复用缓存，失败可重试且不冒充空目录", async () => {
  let fail = true;
  const { result, fetch } = setup({
    optionsReply: async (query) =>
      query.get("date_from") === "2026-10-01" && fail
        ? { ok: false, status: 503, json: async () => ({ error: { code: "UNAVAILABLE" } }) }
        : undefined,
  });
  await waitFor(() => expect(result.current.options.data?.data.products).toHaveLength(125));
  act(() => result.current.change({ from: "2026-10-01" }));
  await waitFor(() => expect(result.current.options.error).toBeTruthy(), { timeout: 4000 });
  expect(result.current.options.data).toBeUndefined();
  fail = false;
  await act(async () => {
    await result.current.options.refetch();
  });
  await waitFor(() => expect(result.current.options.data?.data.products).toHaveLength(125));
  const calls = fetch.mock.calls.filter(([url]) => url.includes("/options?")).length;
  act(() => result.current.change({ from: "2026-10-02" }));
  expect(result.current.options.data?.data.products).toHaveLength(125);
  expect(fetch.mock.calls.filter(([url]) => url.includes("/options?"))).toHaveLength(calls);
});

it("无结果的搜索不清空其他候选或已选条件，清空后恢复全部商品", async () => {
  const { result } = setup();
  await waitFor(() => expect(result.current.options.data).toBeTruthy());
  act(() => result.current.change({ level: "A级" }));
  act(() => result.current.setNeedle("不存在的商品"));
  expect(result.current.options.data?.data.products).toEqual([]);
  expect(result.current.options.data?.data.categories).toHaveLength(2);
  expect(result.current.scope.level).toBe("A级");
  act(() => result.current.setNeedle(""));
  expect(result.current.options.data?.data.products).toHaveLength(125);
});

it("快速切换日期时，晚到的旧目录不会覆盖当前候选", async () => {
  let release!: (value: ResponseLike) => void;
  let requested = false;
  const delayed = new Promise<ResponseLike>((resolve) => {
    release = resolve;
  });
  const { result } = setup({
    optionsReply: async (query) => {
      if (query.get("date_from") === "2026-10-01") {
        requested = true;
        return delayed;
      }
      return undefined;
    },
  });
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(125));
  act(() => result.current.change({ from: "2026-10-01" }));
  await waitFor(() => expect(requested).toBe(true));
  act(() => result.current.change({ from: "2026-10-03" }));
  await waitFor(() => expect(result.current.options.data?.data.total).toBe(125));
  await act(async () =>
    release({
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          shops: [],
          channels: [],
          products: [],
          selected_products: [],
          categories: [],
          levels: [],
          operators: [],
          total: 0,
        },
        meta: { data_version: "v1", request_id: "late" },
      }),
    }),
  );
  expect(result.current.scope.from).toBe("2026-10-03");
  expect(result.current.options.data?.data.total).toBe(125);
});

it("目录被截断时明确失败，不把前100项当作完整选项", async () => {
  const { result, fetch } = setup({
    optionsReply: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          shops: [],
          channels: [],
          products: products.slice(0, 100),
          selected_products: [],
          categories: [],
          levels: [],
          operators: [],
          total: 125,
        },
        meta: { data_version: "v1", request_id: "truncated" },
      }),
    }),
  });
  await waitFor(() => expect(result.current.options.error).toBeTruthy(), { timeout: 4000 });
  expect(result.current.options.data).toBeUndefined();
  expect(fetch.mock.calls.filter(([url]) => url.includes("/options?"))).toHaveLength(3);
});
