// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AftersalesPage } from "./AftersalesPage";
vi.mock("./Charts", () => ({
  Charts: () => <section id="aftersales-charts">历史趋势与原因结构</section>,
}));
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
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
const meta = {
  data_version: "v1",
  status: "source_unavailable",
  default_report_id: null,
  observed_to: null,
  complete_through: null,
  last_success_at: null,
  last_attempt_at: null,
  sources: [
    {
      code: "j1",
      label: "售后原因",
      locator: null,
      status: "source_unavailable",
      observed_from: null,
      observed_to: null,
      last_success_at: null,
      last_attempt_at: null,
      error_code: null,
      note: "缺少可核交付",
    },
  ],
};
const labels = [
  "实退数量",
  "实际入库金额",
  "支付口径GMV",
  "售前退款率",
  "售后退款率",
  "总退款率",
  "售后记录",
  "尺码问题",
  "质量问题",
];
const overview = {
  report: null,
  metrics: labels.map((label, i) => ({
    key: String(i),
    label,
    type: "number",
    value: null,
    detail: "来源待接入",
    note: "非订单数",
    status: "source_unavailable",
  })),
  risk_metrics: ["总退款率", "退款金额", "较上期变化", "目标差距", "预计损失"].map((label) => ({
    key: label,
    label,
    type: "number",
    value: null,
    detail: "待核对",
    note: "",
    status: "pending",
  })),
  decision: {
    title: "数据待核对",
    status: "待验证",
    rate: null,
    amount: null,
    amount_share: null,
    change_pp: null,
    note: "没有来源，不生成经营判断",
  },
  priorities: [],
  actions: [],
  stages: ["未发货退款", "已发货拒收", "签收后退货"].map((name) => ({
    name,
    amount: null,
    qty: null,
    share: null,
    trend: "趋势待补",
    status: "待验证",
    note: "阶段字段待补",
  })),
  size_breakdown: ["偏大", "偏小", "未说明"].map((name) => ({ name, qty: null, share: null })),
  reasons: [],
  trend: [],
  scope_note: "当前报告范围",
  quality_note: "来源尚不可用",
  core_insights: [],
  category_risks: [],
  style_risks: [],
  category_actions: [],
  style_actions: [],
};
function mount(withRows = false, customReport = false) {
  const fetch = vi.fn(async (url: string) => {
    const p = new URL(url, "http://localhost").searchParams;
    const view = p.get("view") || "category";
    const data = url.includes("/meta?")
      ? meta
      : url.includes("/options?")
        ? { reports: [], categories: [], note: "" }
        : url.includes("/overview?")
          ? customReport
            ? {
                ...overview,
                report: {
                  id: "custom-r1",
                  report_type: "custom",
                  label: "期间报告",
                  period_start: "2026-09-28",
                  period_end: "2026-10-27",
                  generated_at: null,
                  document_url: null,
                  sheet_url: null,
                },
              }
            : overview
          : {
              status: "source_unavailable",
              scope_note: `${view}范围说明`,
              columns: [
                { key: "name", label: view === "style" ? "款式编码" : "品类", type: "text" },
              ],
              rows: withRows
                ? [
                    {
                      id: `row-${p.get("page")}`,
                      cells: { name: { value: `记录第${p.get("page")}页` } },
                    },
                  ]
                : [],
              total: withRows ? 26 : 0,
              page: Number(p.get("page") || 1),
              size: 25,
            };
    return { ok: true, status: 200, json: async () => ({ data, meta }) };
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={["/aftersales?focus=quality"]}>
      <QueryClientProvider client={client}>
        <AftersalesPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return fetch;
}
it("完整空态保留共享导航和正文筛选、九卡，缺数据不假零；重置保留标签与折叠", async () => {
  const user = userEvent.setup();
  mount();
  await screen.findByText("没有来源，不生成经营判断");
  expect(screen.getByRole("navigation", { name: "本页目录" })).toBeTruthy();
  const filters = screen.getByRole("region", { name: "售后分析筛选条件" });
  expect(within(filters).getByRole("combobox", { name: "报告周期" })).toBeTruthy();
  expect((within(filters).getByRole("radio", { name: "质量" }) as HTMLInputElement).checked).toBe(
    true,
  );
  await user.click(screen.getByRole("button", { name: "指标与整改明细" }));
  await waitFor(() => expect(screen.getByRole("region", { name: "售后九项指标" })).toBeTruthy());
  expect(
    within(screen.getByRole("region", { name: "售后九项指标" })).getAllByText("—").length,
  ).toBe(9);
  await user.click(screen.getByRole("tab", { name: "单款分析" }));
  await screen.findByText("style范围说明");
  await user.type(within(filters).getByRole("searchbox", { name: "单款/原因搜索" }), "ABC");
  await user.click(within(filters).getByRole("button", { name: "重置筛选" }));
  expect(screen.getByRole("tab", { name: "单款分析" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("button", { name: "指标与整改明细" }).getAttribute("aria-expanded")).toBe(
    "true",
  );
  expect(
    within(filters).getByRole("searchbox", { name: "单款/原因搜索" }).getAttribute("value"),
  ).toBe("");
});
it("请求包含当前标签与后端分页范围，不向上游读取", async () => {
  const user = userEvent.setup();
  const fetch = mount();
  await screen.findByText("category范围说明");
  await user.click(screen.getByRole("tab", { name: "同比分析" }));
  await screen.findByText("yoy范围说明");
  expect(
    fetch.mock.calls.some(
      ([url]) => url.includes("/analysis?") && url.includes("view=yoy") && url.includes("size=25"),
    ),
  ).toBe(true);
  expect(fetch.mock.calls.every(([url]) => url.startsWith("/api/v1/aftersales/"))).toBe(true);
});

it("明细按后端页码请求并显示返回页，筛选改变重回第一页", async () => {
  const user = userEvent.setup();
  const fetch = mount(true);
  await screen.findByText("记录第1页");
  await user.click(screen.getByTitle("下一页"));
  await screen.findByText("记录第2页");
  expect(
    fetch.mock.calls.some(([url]) => url.includes("/analysis?") && url.includes("page=2")),
  ).toBe(true);
  await user.type(screen.getByRole("searchbox", { name: "单款/原因搜索" }), "款号");
  await screen.findByText("记录第1页");
  const last = fetch.mock.calls.filter(([url]) => url.includes("/analysis?")).at(-1)?.[0] ?? "";
  expect(new URL(last, "http://localhost").searchParams.get("keyword")).toBe("款号");
  expect(new URL(last, "http://localhost").searchParams.get("page")).toBe("1");
});

it("自定义30天报告明确滚动窗口且不冒称自然周", async () => {
  mount(false, true);
  await screen.findByText("2026-09-28 至 2026-10-27（滚动30天，非自然周）");
  expect(screen.getByRole("heading", { name: "近30天退货分析 · 滚动报告" })).toBeTruthy();
});
