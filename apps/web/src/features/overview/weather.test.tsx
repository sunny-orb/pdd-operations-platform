// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { useWeather } from "./weather";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const info = (version = "w1", latest = "new") => ({
  data_version: version,
  status: "partial",
  default_publication_id: latest,
  publications: [
    { id: latest, issue_date: "2026-10-28", revision_no: 1, issue_time: "2026-10-28 08:00" },
    { id: "old", issue_date: "2026-10-27", revision_no: 1, issue_time: "2026-10-27 08:00" },
  ],
  last_success_at: null,
  last_attempt_at: null,
  last_attempt_status: null,
  error_code: null,
});
const document = {
  title: "真实公报",
  issue_time: "2026-10-28 08:00",
  focus_regions: [],
  today_rain: { regions: [], detail: "未识别", status: "pending" },
  today_temperature: { regions: [], detail: "未识别", status: "pending" },
  next_7_days: [],
  sources: [],
  forecast_url: "https://www.nmc.cn/",
  reference_url: null,
  group_card_link: null,
  note: "",
};
function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    Wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
it("天气默认跟最新发布，自选历史在新版本后保留，所有请求只访问天气API", async () => {
  let m = info();
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      paths.push(path);
      const u = new URL(path, "http://localhost");
      return {
        ok: true,
        json: async () => ({
          data: u.pathname.endsWith("meta")
            ? m
            : {
                id: u.searchParams.get("publication_id"),
                issue_date:
                  u.searchParams.get("publication_id") === "old" ? "2026-10-27" : "2026-10-28",
                revision_no: 1,
                document,
              },
          meta: m,
        }),
      };
    }),
  );
  const { client, Wrapper } = wrapper();
  const { result } = renderHook(() => useWeather(), { wrapper: Wrapper });
  await waitFor(() => expect(result.current.publication?.id).toBe("new"));
  act(() => result.current.select("old"));
  await waitFor(() => expect(result.current.publication?.id).toBe("old"));
  m = info("w2", "newer");
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "weather", "meta"] });
  });
  await waitFor(() => expect(result.current.meta?.data_version).toBe("w2"));
  await waitFor(() => expect(result.current.publication?.id).toBe("old"));
  expect(result.current.selectedId).toBe("old");
  expect(paths.every((p) => p.startsWith("/api/v1/weather/"))).toBe(true);
});
