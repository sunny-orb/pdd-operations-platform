// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AftersalesPage } from "./AftersalesPage";

vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));
vi.mock("./Charts", () => ({
  Charts: () => <section id="aftersales-charts">历史趋势与原因结构</section>,
}));
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const reports = ["r1", "r2", "r3"].map((id) => ({
  id,
  report_type: "monthly",
  label: `报告${id}`,
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  generated_at: null,
  document_url: null,
  sheet_url: null,
}));
const label = (id: string) => `报告${id} 2026-10-01 至 2026-10-31`;
function setup(sourceStatus = "observed") {
  const pending = new Map<string, (status?: number) => void>();
  const signals = new Map<string, AbortSignal>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const parsed = new URL(url, "http://localhost");
      const path = parsed.pathname.split("/").at(-1)!;
      const id = parsed.searchParams.get("report_id") || "r1";
      const meta = {
        data_version: "v1",
        status: "partial",
        default_report_id: id,
        observed_to: "2026-10-31",
        complete_through: null,
        last_success_at: null,
        last_attempt_at: null,
        sources: [
          {
            code: "returns",
            label: "售后退货",
            locator: null,
            status: sourceStatus,
            observed_from: "2026-10-01",
            observed_to: "2026-10-31",
            last_success_at: null,
            last_attempt_at: null,
            error_code: null,
            note: "",
          },
        ],
      };
      const data =
        path === "meta"
          ? meta
          : path === "options"
            ? { reports, categories: [id], note: "" }
            : path === "overview"
              ? {
                  report: reports.find((report) => report.id === id),
                  metrics: [],
                  risk_metrics: [],
                  decision: {
                    title: `风险${id}`,
                    status: "建议核查",
                    rate: "0.4",
                    amount: "10",
                    amount_share: "0.5",
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
                  scope_note: "",
                  columns: [{ key: "name", label: "品类", type: "text" }],
                  rows: [{ id, cells: { name: { value: `明细${id}` } } }],
                  total: 1,
                  page: 1,
                  size: 25,
                };
      const response = { ok: true, status: 200, json: async () => ({ data, meta }) };
      if (id === "r1") return response;
      signals.set(`${id}/${path}`, init.signal as AbortSignal);
      return new Promise((resolve) =>
        pending.set(`${id}/${path}`, (status = 200) =>
          resolve(
            status === 200
              ? response
              : { ok: false, status, json: async () => ({ error: { code: "FORBIDDEN" } }) },
          ),
        ),
      );
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={["/aftersales"]}>
      <QueryClientProvider client={client}>
        <AftersalesPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { pending, signals };
}

it.each(["observed", "failed_retained", "new_input_pending_report"])(
  "%s下切换保留占位，旧数据不可见，不闪空态且可继续改选",
  async (sourceStatus) => {
    const user = userEvent.setup();
    const { pending, signals } = setup(sourceStatus);
    const oldRisk = await screen.findByRole("heading", { name: "风险r1" });
    const oldRow = await screen.findByText("明细r1");
    const oldAlert = sourceStatus === "observed" ? null : screen.getByRole("alert");
    const choose = async (id: string) => {
      await user.click(screen.getByRole("combobox", { name: "报告周期" }));
      await user.click(await screen.findByRole("option", { name: label(id) }));
    };
    await choose("r2");
    expect(screen.queryByRole("heading", { name: "等待可核售后报告" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "风险r1" })).toBeNull();
    expect(oldRisk.isConnected).toBe(true);
    expect(oldRisk.closest('[aria-hidden="true"]')).toBeTruthy();
    expect(oldRow.isConnected).toBe(true);
    expect(oldRow.closest('[aria-hidden="true"]')).toBeTruthy();
    if (oldAlert) {
      expect(oldAlert.isConnected).toBe(true);
      expect(oldAlert.closest('[aria-hidden="true"]')).toBeTruthy();
    }
    await act(async () => pending.get("r2/meta")!());
    await waitFor(() => expect(pending.has("r2/overview")).toBe(true));
    expect(oldRisk.isConnected).toBe(true);
    // 新目录尚未回来，旧目录仍允许快速改选；业务数据不混用。
    await choose("r3");
    expect(signals.get("r2/overview")?.aborted).toBe(true);
    await act(async () => pending.get("r3/meta")!());
    await waitFor(() => expect(pending.has("r3/analysis")).toBe(true));
    await act(async () => {
      for (const path of ["overview", "analysis", "options"]) pending.get(`r3/${path}`)!();
    });
    expect(await screen.findByRole("heading", { name: "风险r3" })).toBeTruthy();
    expect(screen.getByText("明细r3")).toBeTruthy();
    await act(async () => {
      for (const path of ["overview", "analysis", "options"]) pending.get(`r2/${path}`)?.();
    });
    expect(screen.queryByRole("heading", { name: "风险r2" })).toBeNull();
    expect(screen.getByRole("heading", { name: "风险r3" })).toBeTruthy();
  },
);

it("改选报告请求失败时退出等待并显示重试，不将旧指标当成当前结果", async () => {
  const user = userEvent.setup();
  const { pending } = setup();
  await screen.findByRole("heading", { name: "风险r1" });
  await user.click(screen.getByRole("combobox", { name: "报告周期" }));
  await user.click(await screen.findByRole("option", { name: label("r2") }));
  await act(async () => pending.get("r2/meta")!(403));
  expect(await screen.findByRole("button", { name: "重试本地查询" })).toBeTruthy();
  expect(screen.queryByText("正在更新报告…")).toBeNull();
  expect(screen.queryByRole("heading", { name: "风险r1" })).toBeNull();
});
