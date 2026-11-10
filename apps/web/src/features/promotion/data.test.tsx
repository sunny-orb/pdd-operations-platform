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
import { usePromotion, useResource } from "./data";
import { metadata, metric } from "./contracts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const meta = {
  mode: "store",
  date_from: "2026-08-01",
  date_to: "2026-10-27",
  recommended_from: "2026-09-28",
  recommended_to: "2026-10-27",
  observed_rows: "20",
  complete_through: null,
  completeness: "unknown",
  sources: [],
};
const envelope = (data: unknown, version = "v1") => ({
  ok: true,
  status: 200,
  json: async () => ({ data, meta: { data_version: version, request_id: "test" } }),
});
const wrapper = (entry = "/promotion") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[entry]}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
};
it("保留来源支付日语义与未知点击天数，不把未知天数改成1天", () => {
  const basis = {
    business_date_basis: "payment_date",
    attribution_state: "source_reported_payment_date",
  };
  const parsed = metadata.parse({ ...meta, ...basis, attribution_window_days: null });
  expect(parsed).toMatchObject({ ...basis, attribution_window_days: null });
  expect(
    metric.parse({
      ...basis,
      value: "40",
      status: "observed",
      reason: null,
      label: "交易额",
      type: "currency",
    }),
  ).toMatchObject(basis);
});
it("409仅重取一次元信息，不循环重试同一版本", async () => {
  const fetch = vi.fn(async (url: string) =>
    url.includes("/meta?")
      ? envelope(meta)
      : { ok: false, status: 409, json: async () => ({ error: { code: "VERSION_CONFLICT" } }) },
  );
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => usePromotion(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.overview.error).toBeTruthy());
  await waitFor(() =>
    expect(fetch.mock.calls.filter(([url]) => url.includes("/meta?")).length).toBe(2),
  );
  await act(async () => {
    await result.current.overview.refetch();
  });
  expect(fetch.mock.calls.filter(([url]) => url.includes("/meta?")).length).toBe(2);
});
it("无效日期禁止经营请求；刷新不会触发来源同步", async () => {
  const fetch = vi.fn(async (url: string) =>
    url.includes("/meta?") ? envelope(meta) : envelope({}),
  );
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => usePromotion(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.version).toBe("v1"));
  act(() => result.current.change({ from: "2026-10-27", to: "2026-10-01" }));
  await waitFor(() => expect(result.current.invalid).toContain("结束日期"));
  const calls = fetch.mock.calls.length;
  await act(async () => {
    await result.current.overview.refetch();
  });
  expect(fetch.mock.calls.length).toBe(calls);
  expect(fetch.mock.calls.every(([url]) => url.startsWith("/api/v1/promotion/"))).toBe(true);
});
it("元信息新版本推进默认窗口，自选窗口保留，旧请求收到取消信号", async () => {
  let version = "v1";
  const signals: { url: string; signal: AbortSignal }[] = [];
  const fetch = vi.fn(async (url: string, init?: { signal: AbortSignal }) => {
    if (url.includes("/meta?"))
      return envelope(
        version === "v1"
          ? meta
          : {
              ...meta,
              date_to: "2026-10-28",
              recommended_from: "2026-09-29",
              recommended_to: "2026-10-28",
            },
        version,
      );
    if (init?.signal) signals.push({ url, signal: init.signal });
    return new Promise(() => {});
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => usePromotion(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.scope.to).toBe("2026-10-27"));
  const old = signals.find((entry) => entry.url.includes("/overview?"));
  expect(old).toBeTruthy();
  version = "v2";
  await act(async () => {
    await result.current.meta.refetch();
  });
  await waitFor(() => expect(result.current.scope.to).toBe("2026-10-28"));
  expect(old?.signal.aborted).toBe(true);
  act(() => result.current.change({ from: "2026-10-01", to: "2026-10-05", shop: "663599807" }));
  version = "v3";
  await act(async () => {
    await result.current.meta.refetch();
  });
  expect(result.current.scope).toMatchObject({
    from: "2026-10-01",
    to: "2026-10-05",
    shop: "663599807",
  });
});

it("经营请求保留跨店完整listing身份，目录不按已选商品重新取数", async () => {
  const fetch = vi.fn(async (url: string) =>
    url.includes("/meta?") ? envelope({ ...meta, mode: "product" }) : envelope({}),
  );
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => usePromotion(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.version).toBe("v1"));
  act(() => {
    result.current.setMode("product");
  });
  await waitFor(() => expect(result.current.mode).toBe("product"));
  act(() => result.current.change({ products: ["uuid-listing-shop-a", "uuid-listing-shop-b"] }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url]) =>
          url.includes("/overview?") &&
          url.includes("listing_ids=uuid-listing-shop-a") &&
          url.includes("listing_ids=uuid-listing-shop-b"),
      ),
    ).toBe(true),
  );
});

it("明细翻页只重查明细，业务筛选刷新经营结果并复用日期目录", async () => {
  const fetch = vi.fn(async (url: string) => {
    const query = new URL(url, "http://localhost").searchParams;
    const data = url.includes("/meta?")
      ? { ...meta, mode: "product" }
      : url.includes("/options?")
        ? {
            shops: [],
            channels: [],
            products: [],
            selected_products: [],
            categories: [],
            levels: [],
            operators: [],
            total: 0,
          }
        : url.includes("/days?")
          ? {
              items: [],
              total: 100,
              page: Number(query.get("page")),
              size: Number(query.get("page_size")),
              total_pages: 4,
              row_grain: "day",
            }
          : url.includes("/overview?")
            ? {
                metrics: {},
                net: {},
                series: [],
                funnel: [],
                top5: [],
                coverage: {},
                current_record_count: "100",
                dataset_record_count: "100",
                windows: [],
                channel_summaries: [],
              }
            : {};
    return envelope(data);
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(
    () => {
      const state = usePromotion();
      const diagnosis = useResource(
        "diagnosis",
        z.unknown(),
        state.mode,
        state.queryScope,
        state.version,
        state.invalid,
        [],
        false,
        state.businessReady,
      );
      return { ...state, diagnosis };
    },
    { wrapper: wrapper("/promotion?mode=product") },
  );
  await waitFor(() =>
    expect(
      result.current.overview.isSuccess &&
        result.current.options.isSuccess &&
        result.current.days.isSuccess &&
        result.current.diagnosis.isSuccess,
    ).toBe(true),
  );
  fetch.mockClear();
  act(() => result.current.change({ page: 2, size: 50 }));
  await waitFor(() => expect(result.current.days.data?.data.page).toBe(2));
  expect(fetch.mock.calls.map(([url]) => new URL(url, "http://localhost").pathname)).toEqual([
    "/api/v1/promotion/days",
  ]);
  fetch.mockClear();
  act(() => result.current.change({ shop: "shop-a" }));
  await waitFor(() => expect(fetch.mock.calls).toHaveLength(3));
  expect(fetch.mock.calls.every(([url]) => url.includes("shop_id=shop-a"))).toBe(true);
});

function productQueries(
  reply?: (url: URL, signal?: AbortSignal) => Promise<ReturnType<typeof envelope> | undefined>,
) {
  let version = "v1";
  const requests: { url: URL; signal?: AbortSignal }[] = [];
  const fetch = vi.fn(async (input: string, init?: { signal: AbortSignal }) => {
    const url = new URL(input, "http://localhost");
    requests.push({ url, signal: init?.signal });
    const custom = await reply?.(url, init?.signal);
    if (custom) return custom;
    const data = url.pathname.endsWith("/meta")
      ? { ...meta, mode: "product" }
      : url.pathname.endsWith("/options")
        ? {
            shops: [],
            channels: [],
            products: [],
            selected_products: [],
            categories: [],
            levels: [],
            operators: [],
            total: 0,
          }
        : url.pathname.endsWith("/days")
          ? {
              items: [],
              total: 0,
              page: Number(url.searchParams.get("page")),
              size: 25,
              total_pages: 0,
              row_grain: "day",
            }
          : {
              metrics: {},
              net: {},
              series: [],
              funnel: [],
              top5: [],
              coverage: {},
              current_record_count: url.searchParams.get("shop_id") === "shop-a" ? "1" : "2",
              dataset_record_count: "2",
              windows: [],
              channel_summaries: [],
            };
    return envelope(data, version);
  });
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => usePromotion(), { wrapper: wrapper("/promotion?mode=product") });
  return {
    ...hook,
    requests,
    business: () => requests.filter(({ url }) => /\/(overview|days)$/.test(url.pathname)),
    setVersion: (value: string) => {
      version = value;
    },
  };
}

it("商品筛选即时更新，连续修改只查询最终范围，等待时不呈现旧范围数据", async () => {
  const { result, business } = productQueries();
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("2"));
  const before = business().length;
  vi.useFakeTimers();
  act(() => result.current.change({ shop: "shop-a" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(80);
  });
  act(() => result.current.change({ operator: "小李" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(150);
  });
  expect(business()).toHaveLength(before);
  expect(result.current.scope).toMatchObject({ shop: "shop-a", operator: "小李" });
  expect(result.current.overview.data).toBeUndefined();
  expect(result.current.isScopePending).toBe(true);
  expect(result.current.businessBusy).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  vi.useRealTimers();
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("1"));
  expect(business()).toHaveLength(before + 2);
  for (const { url } of business().slice(before)) {
    expect(url.searchParams.get("shop_id")).toBe("shop-a");
    expect(url.searchParams.get("operator")).toBe("小李");
  }
});

it("同版本经营结果再次选择直接复用，新版本同范围重新读取", async () => {
  const { result, business, setVersion } = productQueries();
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("2"));
  act(() => result.current.change({ shop: "shop-a" }));
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("1"));
  const before = business().length;
  act(() => result.current.change({ shop: "" }));
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("2"));
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 250));
  });
  expect(business()).toHaveLength(before);
  setVersion("v2");
  await act(async () => {
    await result.current.meta.refetch();
  });
  await waitFor(() => expect(result.current.overview.data?.meta.data_version).toBe("v2"));
  expect(business()).toHaveLength(before + 2);
});

it("商品总览请求轻量展示响应，返回经营数值仍按既有契约展示", async () => {
  const { result, business } = productQueries();
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("2"));
  const overviewRequest = business().find(({ url }) => url.pathname.endsWith("/overview"))!;
  expect(overviewRequest.url.searchParams.get("detail")).toBe("display");
  expect(
    business()
      .find(({ url }) => url.pathname.endsWith("/days"))!
      .url.searchParams.has("detail"),
  ).toBe(false);
});

it("同版本跨上海零点后元信息复查更新诊断摘要与清单，经营结果继续复用", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-02T15:59:59Z"));
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = new URL(input, "http://localhost");
      requests.push(url);
      return envelope(
        url.pathname.endsWith("/meta")
          ? { ...meta, mode: "product" }
          : url.pathname.endsWith("/options")
            ? {
                shops: [],
                channels: [],
                products: [],
                selected_products: [],
                categories: [],
                levels: [],
                operators: [],
                total: 0,
              }
            : { checkedAt: new Date().toISOString() },
      );
    }),
  );
  const { result } = renderHook(
    () => {
      const state = usePromotion();
      const summary = useResource(
        "diagnosis",
        z.object({ checkedAt: z.string() }),
        state.mode,
        state.queryScope,
        state.version,
        state.invalid,
        [],
        false,
        state.businessReady,
        { detail: "summary" },
      );
      const objects = useResource(
        "diagnosis/objects",
        z.object({ checkedAt: z.string() }),
        state.mode,
        state.queryScope,
        state.version,
        state.invalid,
        [],
        false,
        state.businessReady,
        { group: "products" },
      );
      return { state, summary, objects };
    },
    { wrapper: wrapper("/promotion?mode=product") },
  );
  await waitFor(() =>
    expect(result.current.summary.data?.data.checkedAt).toBe("2026-11-02T15:59:59.000Z"),
  );
  await waitFor(() => expect(result.current.objects.data).toBeTruthy());
  const oldBusinessCount = requests.filter((url) => /\/(overview|days)$/.test(url.pathname)).length;
  vi.setSystemTime(new Date("2026-11-02T16:00:01Z"));
  await act(async () => {
    await result.current.state.meta.refetch();
  });
  await waitFor(() =>
    expect(result.current.summary.data?.data.checkedAt).toBe("2026-11-02T16:00:01.000Z"),
  );
  expect(result.current.objects.data?.data.checkedAt).toBe("2026-11-02T16:00:01.000Z");
  expect(result.current.state.version).toBe("v1");
  expect(requests.filter((url) => /\/(overview|days)$/.test(url.pathname))).toHaveLength(
    oldBusinessCount,
  );
  expect(requests.filter((url) => url.pathname.includes("/diagnosis"))).toHaveLength(4);
});

it("晚到的旧经营响应被取消且不会在下一次查询等待期间闪回", async () => {
  let release!: (response: ReturnType<typeof envelope>) => void;
  const delayed = new Promise<ReturnType<typeof envelope>>((resolve) => {
    release = resolve;
  });
  const { result, requests } = productQueries(async (url) =>
    url.pathname.endsWith("/overview") && url.searchParams.get("shop_id") === "shop-a"
      ? delayed
      : undefined,
  );
  await waitFor(() => expect(result.current.overview.data).toBeTruthy());
  act(() => result.current.change({ shop: "shop-a" }));
  await waitFor(() =>
    expect(
      requests.some(
        ({ url }) =>
          url.pathname.endsWith("/overview") && url.searchParams.get("shop_id") === "shop-a",
      ),
    ).toBe(true),
  );
  const old = requests.find(
    ({ url }) => url.pathname.endsWith("/overview") && url.searchParams.get("shop_id") === "shop-a",
  )!;
  act(() => result.current.change({ shop: "shop-b" }));
  expect(old.signal?.aborted).toBe(true);
  expect(result.current.overview.data).toBeUndefined();
  await act(async () =>
    release(
      envelope({
        metrics: {},
        net: {},
        series: [],
        funnel: [],
        top5: [],
        coverage: {},
        current_record_count: "999",
        dataset_record_count: "2",
        windows: [],
        channel_summaries: [],
      }),
    ),
  );
  expect(result.current.overview.data).toBeUndefined();
  await waitFor(() => expect(result.current.overview.data?.data.current_record_count).toBe("2"));
  expect(result.current.scope.shop).toBe("shop-b");
});

it("诊断对象的分组搜索和分页隔离查询缓存，摘要额外参数不丢失", async () => {
  const fetch = vi.fn(async (input: string) => envelope({ request: input }));
  vi.stubGlobal("fetch", fetch);
  const base = {
    from: "2026-10-01",
    to: "2026-10-31",
    shop: "",
    channel: "",
    keyword: "",
    category: "",
    level: "",
    operator: "",
    products: [],
    page: 2,
    size: 50,
  };
  const { result, rerender } = renderHook(
    ({ group, search }) =>
      useResource(
        "diagnosis/objects",
        z.object({ request: z.string() }),
        "product",
        base,
        "v1",
        null,
        [],
        false,
        true,
        { group, search, detail: "summary" },
      ),
    { initialProps: { group: "products", search: "雨衣" }, wrapper: wrapper() },
  );
  await waitFor(() => expect(result.current.data).toBeTruthy());
  const query = new URL(result.current.data!.data.request, "http://localhost").searchParams;
  expect(query.get("page")).toBe("2");
  expect(query.get("page_size")).toBe("50");
  expect(query.get("detail")).toBe("summary");
  expect(query.get("search")).toBe("雨衣");
  rerender({ group: "flagged", search: "" });
  await waitFor(() => expect(result.current.data?.data.request).toContain("group=flagged"));
  expect(fetch).toHaveBeenCalledTimes(2);
});
