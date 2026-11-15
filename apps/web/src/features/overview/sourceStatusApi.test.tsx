// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, renderHook, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  dataStatusSchema,
  hasRunningUpdate,
  useDataUpdateStatus,
  type DataStatus,
} from "./sourceStatusApi";
import { sourceHasCheckFailure, sourceHasSyncFailure } from "./dataUpdateAlerts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
});
const idle = () => ({
  data: { sources: [], processing: [], monitor: { enabled: true, interval_seconds: 900 } },
  meta: { generated_at: "2026-11-03T00:00:00Z" },
});
const running = () => ({
  ...idle(),
  data: {
    ...idle().data,
    processing: [
      {
        code: "product_scores",
        name: "商品评分",
        state: "syncing",
        pages: ["products"],
        generated_at: null,
        version_id: null,
        date: null,
        received_at: null,
        last_success_at: null,
        last_attempt_at: null,
        last_finished_at: null,
        attempt_status: "running",
        error_code: null,
        revision: null,
      },
    ],
  },
});
it.each(["ARCHIVE_CAPACITY", "INCREMENTAL_BASELINE_MISMATCH", "WAREHOUSE_CONCURRENCY_LIMIT"])(
  "解析安全失败类别 %s 时保留同步与检查的独立状态",
  (code) => {
    const response = {
      ...idle(),
      data: {
        ...idle().data,
        sources: [
          {
            id: "shop_daily",
            name: "店铺日经营",
            system: "warehouse",
            kind: "daily",
            locator: "cubeappdata.shop_daily",
            table_name: "shop_daily",
            source_title: "店铺日经营",
            source_url: "https://example.com/source",
            pages: ["sales"],
            platform_state: "failed",
            datasets: [
              {
                code: "shop_daily",
                name: "店铺日经营",
                version_id: "retained-v1",
                date: "2026-11-15",
                received_at: null,
                last_success_at: "2026-11-15T08:30:00+08:00",
                last_attempt_at: "2026-11-16T08:30:00+08:00",
                last_finished_at: "2026-11-16T08:31:00+08:00",
                attempt_status: "failed",
                error_code: code,
                revision: null,
              },
            ],
            upstream: {
              state: "unchanged",
              checked_at: "2026-11-16T08:30:00+08:00",
              observed_at: "2026-11-16T08:30:00+08:00",
              web_updated_at: null,
              web_checked_at: null,
              stale: false,
              observation: {},
              accepted_at: "2026-11-15T08:30:00+08:00",
              error_code: code,
              duration_ms: 10,
            },
          },
        ],
      },
    };
    const data = dataStatusSchema.parse(response).data;
    const source = data.sources[0];
    expect(source.upstream.error_code).toBe(code);
    expect(source.datasets[0].error_code).toBe(code);
    expect(source.datasets[0].version_id).toBe("retained-v1");
    expect(sourceHasSyncFailure(source)).toBe(true);
    expect(sourceHasCheckFailure(source)).toBe(false);
    expect(hasRunningUpdate(data)).toBe(false);
  },
);
it("打开立即读取，空闲不轮询；运行中15秒，完成停止；关闭入口60秒，后台及卸载停止", async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  let response = idle() as ReturnType<typeof idle> | ReturnType<typeof running>;
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(response)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const hook = renderHook(({ open }) => useDataUpdateStatus(open), {
    initialProps: { open: false },
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  const advance = async (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  await advance(10);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  hook.rerender({ open: true });
  await advance(10);
  expect(fetcher).toHaveBeenCalledTimes(3);
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(3);
  response = running();
  await act(async () => {
    await hook.result.current.refetch();
  });
  await advance(10);
  expect(fetcher).toHaveBeenCalledTimes(4);
  await advance(15_000);
  expect(fetcher).toHaveBeenCalledTimes(5);
  response = idle();
  await advance(15_000);
  expect(fetcher).toHaveBeenCalledTimes(6);
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(6);
  hook.rerender({ open: false });
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(7);
  focusManager.setFocused(false);
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(7);
  hook.unmount();
  focusManager.setFocused(true);
  await advance(60_000);
  expect(fetcher).toHaveBeenCalledTimes(7);
  client.clear();
});
it("检查/同步/计算才跟踪，等待和失败不算运行", () => {
  const source = (upstream: string, platform: string, attempt: string) =>
    ({
      sources: [
        {
          upstream: { state: upstream },
          platform_state: platform,
          datasets: [{ attempt_status: attempt }],
        },
      ],
      processing: [],
    }) as unknown as DataStatus;
  expect(hasRunningUpdate(source("checking", "available", "succeeded"))).toBe(true);
  expect(hasRunningUpdate(source("unchanged", "syncing", "running"))).toBe(true);
  expect(hasRunningUpdate(source("unchanged", "available", "running"))).toBe(true);
  expect(hasRunningUpdate(source("failed", "pending", "queued"))).toBe(false);
  expect(hasRunningUpdate(running().data)).toBe(true);
});
it("运行状态读取失败后停止快速轮询并保留旧状态", async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  let fail = false;
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () =>
      fail
        ? new Response(JSON.stringify({ error: { code: "DATABASE_BUSY" } }), { status: 503 })
        : new Response(JSON.stringify(running())),
    );
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0, retry: false } } });
  const hook = renderHook(() => ({ ...useDataUpdateStatus(true) }), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  fail = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(hook.result.current.isError).toBe(true);
  expect(hook.result.current.data?.data.processing).toHaveLength(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
  hook.unmount();
  client.clear();
});
