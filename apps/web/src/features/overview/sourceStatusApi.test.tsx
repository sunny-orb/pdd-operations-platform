// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useDataUpdateStatus } from "./sourceStatusApi";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
});
it("弹窗状态查询每15秒读取，后台和关闭后不继续请求", async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    calls.push(String(input));
    return new Response(
      JSON.stringify({
        data: { sources: [], processing: [], monitor: { enabled: true, interval_seconds: 900 } },
        meta: { generated_at: "2026-11-03T00:00:00Z" },
      }),
    );
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const { unmount } = renderHook(useDataUpdateStatus, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(calls).toEqual(["/api/v1/data-status"]);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(calls).toHaveLength(2);
  focusManager.setFocused(false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(calls).toHaveLength(2);
  unmount();
  focusManager.setFocused(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(calls).toHaveLength(2);
  client.clear();
});
