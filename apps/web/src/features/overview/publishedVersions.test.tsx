// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { DomainId } from "./model";
import { useOverview } from "./useOverview";
const calls = vi.hoisted(() => ({ meta: [] as string[], facts: [] as string[] }));
vi.mock("./adapters", () => {
  const spec = (id: DomainId) => ({
    id,
    meta: async () => {
      calls.meta.push(id);
      return {
        id,
        version: "v1",
        date: "2026-11-09",
        completeThrough: null,
        success: null,
        attempt: null,
        attemptStatus: null,
        status: "observed",
        notes: [],
        raw: null,
      };
    },
    load: async () => {
      calls.facts.push(id);
      return {
        card: {
          title: id,
          heading: id,
          value: "1",
          valueType: "number",
          valueLabel: "日值",
          pairs: [],
          note: "",
          link: "/",
          status: "观察",
        },
        observations: [],
        actions: [],
        observationState: "observed",
      };
    },
  });
  return {
    productsSpec: spec("products"),
    salesSpec: spec("sales"),
    promotionSpec: spec("promotion"),
    aftersalesSpec: spec("aftersales"),
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  calls.meta = [];
  calls.facts = [];
});
const versions = { products: "p1", sales: "s1", promotion: "m1", aftersales: "a1" };
async function advance(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(useOverview, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}
it("初次版本检查不重复加载，45秒/聚焦不重查，5分钟仅变更域刷新", async () => {
  vi.useFakeTimers();
  let tokens = { ...versions };
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify({ data: tokens })));
  setup();
  await advance();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(calls.meta).toHaveLength(4);
  expect(calls.facts).toHaveLength(4);
  await advance(45_000);
  window.dispatchEvent(new Event("focus"));
  await advance();
  expect(calls.meta).toHaveLength(4);
  expect(fetch).toHaveBeenCalledTimes(1);
  await advance(255_000);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(calls.meta).toHaveLength(4);
  tokens = { ...versions, sales: "s2" };
  await advance(300_000);
  expect(calls.meta.filter((x) => x === "sales")).toHaveLength(2);
  expect(calls.facts.filter((x) => x === "sales")).toHaveLength(2);
  expect(calls.meta.filter((x) => x !== "sales")).toHaveLength(3);
});
it("后台停止发布检查，返回可见后恢复，卸载取消未完成检查", async () => {
  vi.useFakeTimers();
  let hidden = false;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() =>
    hidden ? "hidden" : "visible",
  );
  const signals: AbortSignal[] = [];
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_path, options) => {
    signals.push(options!.signal as AbortSignal);
    if (signals.length === 1) return new Response(JSON.stringify({ data: versions }));
    return new Promise<Response>(() => {});
  });
  const { unmount } = setup();
  await advance();
  hidden = true;
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await advance(600_000);
  expect(fetch).toHaveBeenCalledTimes(1);
  hidden = false;
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await advance();
  expect(fetch).toHaveBeenCalledTimes(2);
  unmount();
  expect(signals[1].aborted).toBe(true);
});

it("首屏元信息等待首次发布基线，避免旧meta先到而新token被当基线", async () => {
  vi.useFakeTimers();
  let finish: ((response: Response) => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  setup();
  await advance();
  expect(calls.meta).toHaveLength(0);
  await act(async () => {
    finish!(new Response(JSON.stringify({ data: versions })));
  });
  await advance();
  expect(calls.meta).toHaveLength(4);
  expect(calls.facts).toHaveLength(4);
});
it("首次发布基线读取失败仍允许业务meta和指标加载", async () => {
  vi.useFakeTimers();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 422 }));
  setup();
  await advance();
  expect(calls.meta).toHaveLength(4);
  expect(calls.facts).toHaveLength(4);
});

it("首查失败后首次成功须补刷新四域，后续同token不重复刷新", async () => {
  vi.useFakeTimers();
  let failed = true;
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    failed ? new Response("{}", { status: 422 }) : new Response(JSON.stringify({ data: versions })),
  );
  setup();
  await advance();
  expect(calls.meta).toHaveLength(4);
  failed = false;
  await advance(300_000);
  expect(calls.meta).toHaveLength(8);
  await advance(300_000);
  expect(calls.meta).toHaveLength(8);
});
