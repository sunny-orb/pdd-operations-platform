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
import { useAftersales } from "./data";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const meta = (version = "v1", report = "r1") => ({
  data_version: version,
  status: "partial",
  default_report_id: report,
  observed_to: "2026-10-27",
  complete_through: null,
  last_success_at: null,
  last_attempt_at: null,
  sources: [],
});
const response = (data: unknown, m = meta()) => ({
  ok: true,
  status: 200,
  json: async () => ({ data, meta: m }),
});
const wrapper = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={["/aftersales?focus=quality"]}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
};
it("409只重查一次目录，同版不无限循环", async () => {
  const fetch = vi.fn(async (url: string) =>
    url.includes("/meta?")
      ? response(meta())
      : { ok: false, status: 409, json: async () => ({ error: { code: "DATA_VERSION_CHANGED" } }) },
  );
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useAftersales(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.overview.error).toBeTruthy());
  await waitFor(() =>
    expect(fetch.mock.calls.filter(([url]) => url.includes("/meta?")).length).toBe(2),
  );
  await act(async () => {
    await result.current.overview.refetch();
  });
  expect(fetch.mock.calls.filter(([url]) => url.includes("/meta?")).length).toBe(2);
});
it("最新目录推进默认报告、取消旧请求，自选报告保留且只查询本地", async () => {
  let version = "v1";
  const signals: { url: string; signal: AbortSignal }[] = [];
  const fetch = vi.fn(async (url: string, init?: { signal: AbortSignal }) => {
    if (url.includes("/meta?"))
      return response(
        meta(version, version === "v1" ? "r1" : "r2"),
        meta(version, version === "v1" ? "r1" : "r2"),
      );
    if (init?.signal) signals.push({ url, signal: init.signal });
    return new Promise(() => {});
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useAftersales(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.scope.report).toBe("r1"));
  const old = signals.find((s) => s.url.includes("/overview?"));
  expect(old).toBeTruthy();
  version = "v2";
  await act(async () => {
    await result.current.meta.refetch();
  });
  await waitFor(() => expect(result.current.scope.report).toBe("r2"));
  expect(old?.signal.aborted).toBe(true);
  act(() => result.current.change({ report: "r1", category: "帽子", page: 3 }));
  version = "v3";
  await act(async () => {
    await result.current.meta.refetch();
  });
  expect(result.current.scope).toMatchObject({
    report: "r1",
    category: "帽子",
    page: 3,
    focus: "quality",
  });
  expect(fetch.mock.calls.every(([url]) => url.startsWith("/api/v1/aftersales/"))).toBe(true);
});

it("默认近8期图和历史保留多份报告，明确选择才缩到单份", async () => {
  const fetch = vi.fn(async (url: string) => {
    const query = new URL(url, "http://localhost").searchParams;
    const ids = query.has("report_id") ? [query.get("report_id")!] : ["r1", "r2"];
    const data = url.includes("/meta?")
      ? meta()
      : url.includes("/options?")
        ? { reports: [], categories: [], note: "" }
        : url.includes("/overview?")
          ? {
              report: null,
              metrics: [],
              risk_metrics: [],
              decision: {
                title: "",
                status: "",
                rate: null,
                amount: null,
                amount_share: null,
                change_pp: null,
                note: "",
              },
              priorities: [],
              actions: [],
              stages: [],
              size_breakdown: [],
              reasons: [],
              trend: ids.map((id) => ({
                id,
                label: id,
                period_start: "2026-09-28",
                period_end: "2026-10-27",
                qty: "3",
              })),
              scope_note: "",
              quality_note: "",
              core_insights: [],
              category_risks: [],
              style_risks: [],
              category_actions: [],
              style_actions: [],
            }
          : {
              status: "partial",
              scope_note: "报告级历史",
              columns: [],
              rows: ids.map((id) => ({ id, cells: {} })),
              total: ids.length,
              page: 1,
              size: 25,
            };
    return response(data);
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useAftersales(), { wrapper: wrapper() });
  act(() => result.current.change({ view: "history" }));
  await waitFor(() => expect(result.current.overview.data?.data.trend).toHaveLength(2));
  await waitFor(() => expect(result.current.analysis.data?.data.rows).toHaveLength(2));
  expect(
    fetch.mock.calls
      .filter(([url]) => !url.includes("/meta?"))
      .every(([url]) => !new URL(url, "http://localhost").searchParams.has("report_id")),
  ).toBe(true);
  act(() => result.current.change({ report: "r1" }));
  await waitFor(() => expect(result.current.overview.data?.data.trend).toHaveLength(1));
  await waitFor(() => expect(result.current.analysis.data?.data.rows).toHaveLength(1));
});
it("显式历史报告的来源日期与轮询绑定所选报告", async () => {
  const fetch = vi.fn(async (url: string) => {
    const query = new URL(url, "http://localhost").searchParams;
    if (url.includes("/meta?")) {
      const selected = {
        ...meta(),
        observed_to: query.get("report_id") === "old" ? "2026-09-30" : "2026-10-27",
      };
      return response(selected, selected);
    }
    return new Promise(() => {});
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useAftersales(), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.meta.data?.data.observed_to).toBe("2026-10-27"));
  act(() => result.current.change({ report: "old" }));
  await waitFor(() => expect(result.current.meta.data?.data.observed_to).toBe("2026-09-30"));
  expect(fetch.mock.calls.some(([url]) => url.includes("/meta?report_id=old"))).toBe(true);
});

it("分析标签和翻页只读取分析，品类筛选重查指标但复用同报告目录", async () => {
  const fetch = vi.fn(async (url: string) => {
    const query = new URL(url, "http://localhost").searchParams;
    const data = url.includes("/meta?")
      ? meta()
      : url.includes("/options?")
        ? { reports: [], categories: [], note: "" }
        : url.includes("/overview?")
          ? {
              report: null,
              metrics: [],
              risk_metrics: [],
              decision: {
                title: "",
                status: "",
                rate: null,
                amount: null,
                amount_share: null,
                change_pp: null,
                note: "",
              },
              priorities: [],
              actions: [],
              stages: [],
              size_breakdown: [],
              reasons: [],
              trend: [],
              scope_note: "",
              quality_note: "",
              core_insights: [],
              category_risks: [],
              style_risks: [],
              category_actions: [],
              style_actions: [],
            }
          : {
              status: "partial",
              scope_note: query.get("view"),
              columns: [],
              rows: [],
              total: 100,
              page: Number(query.get("page")),
              size: Number(query.get("size")),
            };
    return response(data);
  });
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(() => useAftersales(), { wrapper: wrapper() });
  await waitFor(() =>
    expect(
      result.current.overview.isSuccess &&
        result.current.options.isSuccess &&
        result.current.analysis.isSuccess,
    ).toBe(true),
  );
  fetch.mockClear();
  act(() => result.current.change({ view: "style", page: 2, size: 50 }));
  await waitFor(() => expect(result.current.analysis.data?.data.page).toBe(2));
  expect(fetch.mock.calls.map(([url]) => new URL(url, "http://localhost").pathname)).toEqual([
    "/api/v1/aftersales/analysis",
  ]);
  fetch.mockClear();
  act(() => result.current.change({ category: "帽子" }));
  await waitFor(() => expect(fetch.mock.calls).toHaveLength(2));
  expect(fetch.mock.calls.every(([url]) => url.includes("category=%E5%B8%BD%E5%AD%90"))).toBe(true);
});
