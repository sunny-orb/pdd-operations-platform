// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Filters } from "./Filters";
import { useAftersales } from "./data";

vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));

const ids = ["143ac75d-3e82-439c-b3b2-70bb1b4920b2", "76f78a77-88af-4d23-a5f6-aa0ebec618f0"];
const reports = ids.map((id, index) => ({
  id,
  report_type: "weekly",
  label: "近30天退货分析 · 周报",
  period_start: index ? "2026-10-11" : "2026-10-18",
  period_end: index ? "2026-11-09" : "2026-11-16",
  generated_at: null,
  document_url: null,
  sheet_url: null,
}));
const period = (index: number) => `${reports[index].period_start} 至 ${reports[index].period_end}`;
const label = (index: number) => `${reports[index].label} ${period(index)}`;
const note = "报告期间与来源完整性分别显示；缺少分类时不从款号推定。";
const meta = {
  data_version: "v1",
  status: "partial",
  default_report_id: ids[0],
  observed_to: "2026-11-16",
  complete_through: null,
  last_success_at: null,
  last_attempt_at: null,
  sources: [],
};
function response(data: unknown) {
  return { ok: true, status: 200, json: async () => ({ data, meta }) };
}
function Harness() {
  const state = useAftersales();
  return (
    <>
      <Filters state={state} />
      <output data-testid="options-status">{state.options.status}</output>
    </>
  );
}
function mount(url = "/aftersales") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[url]}>
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each([200, 403])(
  "切换报告时，分别等待元信息和选项后返回%s，仍显示已选名称而非ID",
  async (status) => {
    const user = userEvent.setup();
    let releaseMeta!: (value: ReturnType<typeof response>) => void;
    let releaseOptions!: (value: ReturnType<typeof response>) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const query = new URL(url, "http://localhost").searchParams;
        if (url.includes("/meta?")) {
          if (query.get("report_id") === ids[1])
            return new Promise((resolve) => {
              releaseMeta = resolve;
            });
          return response(meta);
        }
        if (url.includes("/options?")) {
          if (query.get("report_id") === ids[1])
            return new Promise((resolve) => {
              releaseOptions = resolve;
            });
          return response({ reports, categories: ["童帽"], note });
        }
        return new Promise(() => {});
      }),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("options-status").textContent).toBe("success"));
    const select = screen.getByRole("combobox", { name: "报告周期" });
    const selectedText = () => select.closest(".ant-select")!.textContent;
    expect(selectedText()).toBe("最新报告");
    await user.click(select);
    expect(screen.getByRole("option", { name: "最新报告" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    await user.click(await screen.findByRole("option", { name: label(1) }));
    expect(selectedText()).toContain(period(1));
    expect(selectedText()).not.toContain(ids[1]);
    await act(async () => releaseMeta(response(meta)));
    await waitFor(() => expect(releaseOptions).toBeTypeOf("function"));
    expect(selectedText()).toContain(period(1));
    expect(selectedText()).not.toContain(ids[1]);
    await act(async () =>
      releaseOptions(
        status === 200
          ? response({ reports, categories: [], note })
          : ({
              ok: false,
              status,
              json: async () => ({ error: { code: "FORBIDDEN" } }),
            } as unknown as ReturnType<typeof response>),
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("options-status").textContent).toBe(
        status === 200 ? "success" : "error",
      ),
    );
    expect(selectedText()).toContain(period(1));
    expect(selectedText()).not.toContain(ids[1]);
    await user.click(screen.getByRole("button", { name: "重置筛选" }));
    await waitFor(() => expect(selectedText()).toBe("最新报告"));
  },
);

it("直接打开带报告ID的链接，加载前不显示内部ID，返回后显示完整名称", async () => {
  let releaseMeta!: (value: ReturnType<typeof response>) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/meta?"))
        return new Promise((resolve) => {
          releaseMeta = resolve;
        });
      if (url.includes("/options?")) return response({ reports, categories: [], note });
      return new Promise(() => {});
    }),
  );
  mount(`/aftersales?report_id=${ids[1]}`);
  const select = screen.getByRole("combobox", { name: "报告周期" });
  expect(select.closest(".ant-select")!.textContent).not.toContain(ids[1]);
  await waitFor(() => expect(releaseMeta).toBeTypeOf("function"));
  await act(async () => releaseMeta(response(meta)));
  await screen.findByText(period(1));
  expect(screen.queryByText(note)).toBeNull();
});
