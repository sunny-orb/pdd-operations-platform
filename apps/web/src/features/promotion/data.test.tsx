// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { usePromotion } from "./data";

afterEach(() => {
  cleanup();
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
const wrapper = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
};
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

it("级联候选请求保留已选完整listing身份，不扩大品类和运营范围", async () => {
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
          url.includes("/options?") &&
          url.includes("listing_ids=uuid-listing-shop-a") &&
          url.includes("listing_ids=uuid-listing-shop-b"),
      ),
    ).toBe(true),
  );
});
