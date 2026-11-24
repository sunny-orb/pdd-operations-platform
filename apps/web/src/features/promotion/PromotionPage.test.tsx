// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PromotionPage } from "./PromotionPage";
import { RegisterScope } from "../../shared/assistant/scopeContext";
vi.mock("./Trend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./Trend")>()),
  Trend: () => <div>趋势图</div>,
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
const metric = (value: string | null, label: string, type = "currency") => ({
  value,
  label,
  type,
  reason: value == null ? "来源缺失" : null,
  status: value == null ? "unavailable" : "available",
  change: null,
});
const metrics = Object.fromEntries(
  ["spend", "revenue", "roi", "impressions", "clicks", "ctr", "cvr", "cpc"].map((key) => [
    key,
    metric("12.12", key),
  ]),
);
function mount({
  empty = false,
  failed = false,
  missing = true,
  product = false,
  overviewData = {},
  selectedShop = "",
  selection = false,
  register = vi.fn(),
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const mode = url.includes("mode=product") ? "product" : "store";
      const data = url.includes("/meta?")
        ? {
            mode,
            date_from: "2026-08-01",
            date_to: "2026-10-27",
            recommended_from: "2026-09-28",
            recommended_to: "2026-10-27",
            observed_rows: "20",
            complete_through: null,
            completeness: "unknown",
            sources: [
              {
                table: "cubeappdata.ods_rpa_pdd_product_promotion_overview_f",
                version: "source-version",
                latest_attempt: { status: failed ? "failed" : "succeeded" },
              },
            ],
          }
        : url.includes("/options?")
          ? {
              shops: [
                { value: "shop-a", label: "甲店" },
                { value: "shop-b", label: "乙店" },
              ],
              channels: [],
              products: selection
                ? [
                    {
                      listing_id: "listing-a",
                      value: "listing-a",
                      product_id: "123",
                      label: "商品甲",
                      product_name: "商品甲",
                      shop_id: "shop-a",
                    },
                  ]
                : [],
              selected_products: [],
              categories: [],
              levels: [],
              operators: [],
              total: selection ? 1 : 0,
            }
          : url.includes("/days?")
            ? { items: [], total: 0, page: 1, size: 25, total_pages: 1, row_grain: "day" }
            : url.includes("/diagnosis?")
              ? {
                  current_window: {},
                  previous_window: {},
                  overall: null,
                  products: [],
                  flagged_products: [],
                  focused_products: [],
                  shops: [],
                  categories: [],
                  counts: {},
                  coverage: {
                    stale: true,
                    observed_through: "2026-10-20",
                    covered_days: 12,
                    missing_dates: [],
                  },
                  rule_version: "SOP v1.4",
                  rule_states: {},
                  status: "insufficient",
                }
              : {
                  metrics: empty
                    ? Object.fromEntries(
                        Object.entries(metrics).map(([key]) => [key, metric(null, key)]),
                      )
                    : metrics,
                  net: {
                    net_revenue: metric(null, "净交易额"),
                    net_roi: metric(null, "净投产比", "ratio"),
                  },
                  series: [],
                  funnel: [],
                  top5: [],
                  coverage: {
                    missing_count: empty || !missing ? 0 : 1,
                    missing:
                      empty || !missing
                        ? []
                        : [
                            {
                              date: "2026-10-27",
                              shop_name: "测试店",
                              channel_label: "明星店铺",
                              reason: "暂无记录",
                            },
                          ],
                    completeness: "unknown",
                    complete_through: null,
                  },
                  current_record_count: empty ? "0" : "20",
                  dataset_record_count: "20",
                  windows: [],
                  channel_summaries: [],
                  ...overviewData,
                };
      return {
        ok: true,
        json: async () => ({ data, meta: { data_version: "v1", request_id: "test" } }),
      };
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        product
          ? "/promotion?mode=product" +
            (selection ? "&product_products=listing-a&product_shop=" + selectedShop : "")
          : "/promotion",
      ]}
    >
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <RegisterScope.Provider value={register}>
          <PromotionPage />
        </RegisterScope.Provider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
it("默认店铺模式保留八指标、净额缺失、缺日和十列表格；指标不能全部取消", async () => {
  mount();
  await screen.findByText("拼多多店铺推广看板");
  const directory = screen.getByRole("navigation", { name: "本页目录" });
  expect(within(directory).getByRole("link", { name: "筛选范围" }).getAttribute("href")).toBe(
    "#promotion-filters",
  );
  expect(within(directory).getByRole("link", { name: "推广指标" }).getAttribute("href")).toBe(
    "#promotion-metrics",
  );
  expect(screen.getByRole("banner", { name: "看板导航" })).toBeTruthy();
  expect(document.getElementById("promotion-filters")).toBeTruthy();
  await screen.findByText("部分日期暂无推广数据。");
  expect(screen.getByText(/净交易额：—/)).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "来源" })).toBeTruthy();
  const spend = screen.getByRole("button", { name: /^总花费/ });
  const revenue = screen.getByRole("button", { name: /^交易额/ });
  await userEvent.click(spend);
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(screen.getByRole("link", { name: "查看日期" }));
  await waitFor(() =>
    expect((document.getElementById("promotion-missing-days") as HTMLDetailsElement).open).toBe(
      true,
    ),
  );
  expect(screen.getByText(/测试店 · 明星店铺/)).toBeTruthy();
});
it("所选店铺渠道每天都有记录时说明记录情况，不重复筛选时间或声称数据完整", async () => {
  mount({ missing: false });
  await screen.findByText("所选期间每天均有推广记录。");
  expect(screen.getByText("查看单个商品，请切换「商品推广明细」。")).toBeTruthy();
  expect(screen.queryByText(/当前统计：/)).toBeNull();
  expect(screen.queryByRole("link", { name: "查看日期" })).toBeNull();
});
it("无推广记录作为普通空态，页头仍可筛选且不显示技术状态或故障警告", async () => {
  mount({ empty: true });
  await screen.findByText("所选期间暂无推广数据");
  expect(screen.getByLabelText("开始日期")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText(/来源与更新状态/)).toBeNull();
  expect(screen.queryByText(/完整截至日/)).toBeNull();
});
it("同步失败保留上次数据并单独提示，不能当成普通无记录", async () => {
  mount({ failed: true });
  await screen.findByText(/本次更新未完成/);
  expect(screen.getByRole("alert").textContent).toContain("2026-10-27");
  expect(screen.queryByText("所选期间暂无推广数据")).toBeNull();
});
it.each(["开始日期", "结束日期"])(
  "%s的日历使用中文月份和星期，选择后仍按年月日提交",
  async (label) => {
    mount();
    await screen.findByText("部分日期暂无推广数据。");
    const input = screen.getByLabelText(label) as HTMLInputElement;
    await userEvent.click(input);
    const calendar = document.querySelector(".ant-picker-dropdown") as HTMLElement;
    expect(within(calendar).getByText("一", { selector: "th" })).toBeTruthy();
    expect(calendar.querySelector(".ant-picker-month-btn")?.textContent).toMatch(/月$/);
    await userEvent.click(within(calendar).getByTitle("2026-09-29"));
    await waitFor(() => expect(input.value).toBe("2026-09-29"));
  },
);
it("商品模式包含漏斗、日期参考、SOP、四层清单及浏览进度；共享导航保留", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "商品推广明细" }));
  await screen.findByText("拼多多推广数据看板");
  const directory = screen.getByRole("navigation", { name: "本页目录" });
  expect(within(directory).getByRole("link", { name: "SOP经营诊断" }).getAttribute("href")).toBe(
    "#promotion-diagnosis",
  );
  expect(within(directory).getByRole("link", { name: "每日推广明细" }).getAttribute("href")).toBe(
    "#promotion-days",
  );
  await screen.findByText("从曝光到成交");
  expect(screen.getByText("投产比最低的5天")).toBeTruthy();
  expect(screen.getByText("从推广结果定位到商品与行动")).toBeTruthy();
  expect(screen.getByText(/^全部商品扫描（/, { selector: "summary" })).toBeTruthy();
  expect(screen.getByRole("slider", { name: "推广明细上下浏览进度" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /返回运营看板中心/ })).toBeTruthy();
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", {
        name: /^(总花费|交易额|实际投产比|曝光量|点击量|点击率|点击转化率|CPC)/,
      }),
    ).toHaveLength(8),
  );
});

it("历史诊断不因最新源日陈旧而展示整块暂停或推算漏记录", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "商品推广明细" }));
  await screen.findByText(/^全部商品扫描（/, { selector: "summary" });
  expect(screen.queryByText(/尚未更新至昨日|缺少.*个日记录|暂停趋势评级/)).toBeNull();
  await userEvent.click(screen.getByText("查看判断规则"));
  expect(screen.getByText(/某天没有记录，照常统计已有数据/)).toBeTruthy();
});

it("商品摘要按实际明细天数计数，部分花费也正常显示线上口径ROI", async () => {
  mount({
    product: true,
    overviewData: {
      coverage: { observed_days: 2, missing_count: 12 },
      metrics: {
        ...metrics,
        roi: { ...metric("7.65", "实际投产比", "ratio"), status: "partial" },
      },
      roi_exclusions: {
        missing_spend_rows: 1,
        missing_revenue_rows: 0,
        revenue_without_spend: "100.25",
      },
    },
  });
  const summary = await screen.findByRole("region", { name: "推广经营结论" });
  await waitFor(() => expect(summary.textContent).toContain("7.65"));
  expect(summary.textContent).toContain("当前范围 2 天数据");
  expect(summary.textContent).not.toContain("参考投产比");
  expect(summary.textContent).not.toContain("无法计算");
  expect(summary.textContent).toContain("推广效率需结合周期变化与商品利润底线判断");
  expect(summary.textContent).not.toContain("当前统计");
  expect(summary.textContent).not.toContain("20 条记录");
  expect(
    within(screen.getByRole("region", { name: "推广八项指标" })).getByRole("button", {
      name: /^实际投产比/,
    }),
  ).toBeTruthy();
});

it.each([
  { value: "0", status: "observed", spendMissing: 0, revenueMissing: 0, excluded: "0", note: null },
  {
    value: null,
    status: "zero_denominator",
    spendMissing: 0,
    revenueMissing: 0,
    excluded: "0",
    note: "花费为0，暂不计算投产比",
  },
  {
    value: null,
    status: "missing",
    spendMissing: 2,
    revenueMissing: 0,
    excluded: "100.25",
    note: "当前范围未提供花费，暂不计算投产比",
  },
  {
    value: null,
    status: "missing_input",
    spendMissing: 1,
    revenueMissing: 0,
    excluded: "0",
    note: "当前范围未提供花费，暂不计算投产比",
  },
  {
    value: null,
    status: "missing_input",
    spendMissing: 0,
    revenueMissing: 1,
    excluded: "0",
    note: "当前范围未提供交易额，暂不计算投产比",
  },
])(
  "商品摘要区分真实0、零分母、空字段，不把缺日当ROI缺项：$status/$spendMissing/$revenueMissing",
  async ({ value, status, spendMissing, revenueMissing, excluded, note }) => {
    mount({
      product: true,
      overviewData: {
        coverage: { observed_days: 2, missing_count: 8 },
        metrics: {
          ...metrics,
          spend: metric(spendMissing ? null : "10", "总花费"),
          revenue: metric(revenueMissing ? null : "100", "交易额"),
          roi: { ...metric(value, "实际投产比", "ratio"), status },
        },
        roi_exclusions: {
          missing_spend_rows: spendMissing,
          missing_revenue_rows: revenueMissing,
          revenue_without_spend: excluded,
        },
      },
    });
    const summary = await screen.findByRole("region", { name: "推广经营结论" });
    await waitFor(() => expect(summary.textContent).toContain("当前范围 2 天数据"));
    if (note) expect(summary.textContent).toContain(note);
    else {
      expect(summary.textContent).not.toContain("未计入");
      expect(summary.textContent).not.toContain("参考投产比");
      expect(summary.textContent).toContain("0.00");
    }
  },
);

it("交易额全空但花费有合计时，准确说明交易额缺项", async () => {
  mount({
    product: true,
    overviewData: {
      metrics: {
        ...metrics,
        spend: { ...metric("20", "总花费"), status: "partial" },
        revenue: { ...metric(null, "交易额"), status: "missing" },
        roi: { ...metric(null, "实际投产比", "ratio"), status: "missing" },
      },
      roi_exclusions: {
        missing_spend_rows: 1,
        missing_revenue_rows: 2,
        revenue_without_spend: "0",
      },
    },
  });
  const summary = await screen.findByRole("region", { name: "推广经营结论" });
  await waitFor(() => expect(summary.textContent).toContain("当前范围未提供交易额"));
  expect(summary.textContent).not.toContain("当前范围未提供花费");
});

it("部分花费保留金额环比，净投产及CPC照常显示线上口径数值", async () => {
  mount({
    product: true,
    overviewData: {
      metrics: {
        ...metrics,
        spend: { ...metric("20", "总花费"), change: "1", comparison_status: "reference" },
        impressions: {
          ...metric("30", "曝光量", "number"),
          change: "0.5",
          comparison_status: "available",
        },
        cpc: { ...metric("0.27", "CPC"), status: "partial" },
      },
      net: {
        net_revenue: metric("28", "净交易额"),
        net_roi: { ...metric("6.65", "净投产比", "ratio"), status: "partial" },
      },
      roi_exclusions: {
        missing_spend_rows: 1,
        missing_revenue_rows: 0,
        revenue_without_spend: "100.25",
      },
    },
  });
  const cards = await screen.findByRole("region", { name: "推广八项指标" });
  await waitFor(() => expect(cards.textContent).toContain("较前期 +100.00%"));
  expect(cards.textContent).toContain("较前期 +50.00%");
  expect(cards.textContent).toContain("净投产比 6.65");
  expect(within(cards).getByRole("button", { name: /^CPC/ }).textContent).toContain("¥0.27");
  expect(screen.getByRole("region", { name: "推广经营结论" }).textContent).not.toContain(
    "无法计算",
  );
});

it.each([
  ["zero_base", "前期为0"],
  ["no_previous", "前期暂无可比数值"],
  ["no_current", "本期暂无可比数值"],
  ["history_short", "前期数据不足"],
])("较前期不能算时具体说明%s", async (status, note) => {
  mount({
    product: true,
    overviewData: {
      metrics: { ...metrics, spend: { ...metric("2", "总花费"), comparison_status: status } },
    },
  });
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "推广八项指标" }).textContent).toContain(note),
  );
});

it.each([
  ["shop-a", false],
  ["shop-b", true],
] as const)("问答保留所选店铺 %s，仅绑定实际有记录的单款", async (selectedShop, empty) => {
  const register = vi.fn();
  mount({ product: true, selectedShop, selection: true, empty, register });
  await waitFor(
    () =>
      expect(register).toHaveBeenLastCalledWith({
        page: "推广分析",
        context: expect.objectContaining({
          shop_id: selectedShop,
          promotion_version: "v1",
          date_from: "2026-09-28",
          date_to: "2026-10-27",
        }),
        subject: empty
          ? null
          : expect.objectContaining({
              listing_id: "listing-a",
              shop_id: "shop-a",
              shop_name: "甲店",
              product_id: "123",
            }),
      }),
    { timeout: 5500 },
  );
});
