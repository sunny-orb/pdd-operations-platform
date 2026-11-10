// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { useProducts } from "../../features/products/data";
import { usePromotion } from "../../features/promotion/data";
import { useAftersales } from "../../features/aftersales/data";
import { useSalesData } from "../../features/sales/api/queries";
import { waitForPublicationBaseline } from "./publishedVersions";
const tokens = { products: "p1", promotion: "m1", sales: "s1", aftersales: "a1" };
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
function setup() {
  const cache = client();
  return renderHook(
    () => ({
      products: useProducts(),
      promotion: usePromotion(),
      aftersales: useAftersales(),
      sales: useSalesData(),
    }),
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={cache}>
          <MemoryRouter>{children}</MemoryRouter>
        </QueryClientProvider>
      ),
    },
  );
}
async function advance() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30);
  });
}
it.each([200, 422])(
  "四子页共享首屏基线，基线HTTP%s完成后才发各域meta且失败不阻断",
  async (status) => {
    vi.useFakeTimers();
    let release: ((response: Response) => void) | undefined;
    const paths: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (path) => {
      paths.push(String(path));
      if (String(path) === "/api/v1/published-versions")
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      return new Response(JSON.stringify({ error: { code: "TEST", message: "测试边界" } }), {
        status: 422,
      });
    });
    setup();
    await advance();
    expect(paths).toEqual(["/api/v1/published-versions"]);
    await act(async () => {
      release!(new Response(JSON.stringify({ data: tokens }), { status }));
    });
    await advance();
    expect(paths.map((path) => new URL(path, "http://local").pathname).sort()).toEqual([
      "/api/v1/aftersales/meta",
      "/api/v1/products/meta",
      "/api/v1/promotion/meta",
      "/api/v1/published-versions",
      "/api/v1/sales/filters",
    ]);
  },
);
it("已缓存基线直接复用，已取消的业务请求不启动发布检查", async () => {
  const cache = client();
  const fetch = vi.spyOn(globalThis, "fetch");
  cache.setQueryData(["overview", "published-versions"], tokens);
  await waitForPublicationBaseline(cache, new AbortController().signal);
  const aborted = new AbortController();
  aborted.abort();
  await expect(waitForPublicationBaseline(client(), aborted.signal)).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(fetch).not.toHaveBeenCalled();
  cache.clear();
});
it("等待基线时离页会取消检查，不能晚到后继续读取业务meta", async () => {
  vi.useFakeTimers();
  const paths: string[] = [];
  let signal: AbortSignal | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (path, options) => {
    paths.push(String(path));
    signal = options!.signal as AbortSignal;
    return new Promise<Response>((_resolve, reject) =>
      signal!.addEventListener("abort", () => reject(signal!.reason), { once: true }),
    );
  });
  const { unmount } = setup();
  await advance();
  unmount();
  await advance();
  expect(signal?.aborted).toBe(true);
  expect(paths).toEqual(["/api/v1/published-versions"]);
});

it("四子页首查失败放行后，首次成功发布基线各刷新一次且同token不再刷新", async () => {
  vi.useFakeTimers();
  let failed = true;
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (path) => {
    paths.push(String(path));
    if (String(path) === "/api/v1/published-versions")
      return failed
        ? new Response("{}", { status: 422 })
        : new Response(JSON.stringify({ data: tokens }));
    return new Response(JSON.stringify({ error: { code: "TEST", message: "测试边界" } }), {
      status: 422,
    });
  });
  setup();
  await advance();
  expect(paths.filter((path) => path !== "/api/v1/published-versions")).toHaveLength(4);
  failed = false;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300_000);
  });
  // 销售首次filters失败时尚无过滤器；发布恢复也应允许重新读取。
  expect(paths.filter((path) => path !== "/api/v1/published-versions")).toHaveLength(8);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300_000);
  });
  expect(paths.filter((path) => path !== "/api/v1/published-versions")).toHaveLength(8);
});

it("基线503耗尽两次重试后，业务meta重试不能再放大轻量请求", async () => {
  vi.useFakeTimers();
  const cache = client();
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 503 }));
  const first = waitForPublicationBaseline(cache, new AbortController().signal);
  await vi.advanceTimersByTimeAsync(3100);
  await first;
  expect(fetch).toHaveBeenCalledTimes(3);
  const again = waitForPublicationBaseline(cache, new AbortController().signal);
  await vi.advanceTimersByTimeAsync(3100);
  await again;
  expect(fetch).toHaveBeenCalledTimes(3);
  cache.clear();
});
