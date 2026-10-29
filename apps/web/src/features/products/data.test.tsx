// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { z } from "zod";
import { useProducts, useResource, type ProductsState } from "./data";
import { readScope } from "./scope";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
