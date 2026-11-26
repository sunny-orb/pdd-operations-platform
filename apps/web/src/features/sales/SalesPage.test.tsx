// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render as renderView,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));
import { SalesCoverage } from "./components/SalesCoverage";
import type { SalesResult } from "./api/contracts";
import { testMeta, testMetrics } from "./testFixtures";
import { SalesPage } from "./SalesPage";
import { syntheticResponse, testShops, syntheticAllShops } from "./testFixtures";
import { _resetCacheForTesting } from "./api/queries";
import { RegisterScope } from "../../shared/assistant/scopeContext";

function render(ui: Parameters<typeof renderView>[0]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderView(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => computed(element));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  _resetCacheForTesting();
});

it("销售助手采用已应用的店铺、目标月份、期间和已发布版本", async () => {
  vi.stubGlobal("fetch", async (path: string) => ({
    ok: true,
    json: async () => syntheticResponse(path),
  }));
  const register = vi.fn();
  render(
    <MemoryRouter
      initialEntries={["/sales?month=2026-09&from=2026-09-15&to=2026-09-16&shop=spihw"]}
    >
      <RegisterScope.Provider value={register}>
        <SalesPage />
      </RegisterScope.Provider>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(register).toHaveBeenLastCalledWith({
      page: "店铺销售",
      selectionLabel: "户外店",
      subject: null,
      context: {
        domain: "sales",
        shop_id: "spihw",
        month: "2026-09",
        date_from: "2026-09-15",
        date_to: "2026-09-16",
        versions: { sales: "synthetic-v1" },
      },
    }),
  );
});

it("八卡按同一首日比较涨跌，保留零基准、缺基准及趋势重试的原值", async () => {
  const metricKeys = [
    "shop_revenue",
    "shop_visitors",
    "buyer_conversion",
    "aov",
    "refund_rate",
    "promotion_spend",
    "promotion_ctr",
    "promotion_roi",
  ];
  const observed = (value: string) => ({ value, state: "observed", observed: 1, expected: 1 });
  const missing = { value: null, state: "no_records", observed: 0, expected: 1 };
  const points = [
    { shop_revenue: "100.00", shop_visitors: "0", promotion_roi: "2.00", promotion_ctr: "0.03" },
    {
      shop_revenue: "120.00",
      shop_visitors: "20",
      buyer_conversion: "0.06",
      promotion_roi: "3.00",
      refund_rate: "0",
    },
    {
      shop_revenue: "0.00",
      shop_visitors: "10",
      buyer_conversion: "0.05",
      refund_rate: "0.0125",
      promotion_ctr: "0.04",
    },
  ].map((values: Record<string, string | undefined>, index) => ({
    date: `2026-10-${25 + index}`,
    metrics: Object.fromEntries(
      metricKeys.map((key) => [key, values[key] == null ? missing : observed(values[key])]),
    ),
  }));
  let attempts = 0;
  const request = vi.fn(async (path: string) => {
    if (path.includes("/performance/trend?")) {
      attempts++;
      if (attempts === 1) return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, json: async () => ({ meta: testMeta, data: { points } }) };
    }
    return { ok: true, json: async () => syntheticResponse(path) };
  });
  vi.stubGlobal("fetch", request);
  render(
    <MemoryRouter initialEntries={["/sales?from=2026-10-25&to=2026-10-27&shop=spigf"]}>
      <SalesPage />
    </MemoryRouter>,
  );
  const trend = screen.getByRole("region", { name: "经营趋势" });
  const kpis = within(screen.getByTestId("sales-kpis"));
  const retryTrend = await within(trend).findByRole("button", { name: "重试读取经营趋势" });
  expect(kpis.getByText("¥31")).toBeTruthy();
  fireEvent.click(retryTrend);
  const details = await within(trend).findByText("查看逐日数据");
  fireEvent.click(details);
  const compared = await within(trend).findByRole("table", { name: "多指标逐日数据" });
  const day = within(within(compared).getByRole("row", { name: /2026-10-26/ }));
  expect(day.getByText("¥120.00")).toBeTruthy();
  expect(day.getByText("+20.00%")).toBeTruthy();
  expect(day.getByText("20 人")).toBeTruthy();
  expect(day.getByText("6.00%")).toBeTruthy();
  expect(day.getByText("较前一日 —")).toBeTruthy();
  const nextDay = within(within(compared).getByRole("row", { name: /2026-10-27/ }));
  expect(nextDay.getByText("5.00%")).toBeTruthy();
  expect(nextDay.getByText("较前一日 -1.00 个百分点")).toBeTruthy();
  expect(within(trend).getByText("首日为 0")).toBeTruthy();
  expect(within(trend).getByText("首日无值")).toBeTruthy();
  fireEvent.click(kpis.getByRole("button", { name: "查看 退款金额占比 趋势" }));
  fireEvent.click(kpis.getByRole("button", { name: "查看 点击率 趋势" }));
  expect(nextDay.getByText("较前一日 +1.25 个百分点")).toBeTruthy();
  expect(nextDay.getByRole("cell", { name: /4\.00%.*较前一日 —/ })).toBeTruthy();
  expect(within(compared).getByText("¥0.00")).toBeTruthy();
  expect(within(compared).getByText("-100.00%")).toBeTruthy();
  const roi = kpis.getByRole("button", { name: "查看 ROI 趋势" });
  const sales = kpis.getByRole("button", { name: "查看 销售金额 趋势" });
  fireEvent.click(roi);
  expect(roi.getAttribute("aria-pressed")).toBe("true");
  expect(sales.getAttribute("aria-pressed")).toBe("true");
  expect(day.getByText("3.00 倍")).toBeTruthy();
  expect(day.getByText("+50.00%")).toBeTruthy();
  fireEvent.click(sales);
  expect(sales.getAttribute("aria-pressed")).toBe("false");
  expect(day.queryByText("¥120.00")).toBeNull();
  expect(attempts).toBe(2);
});

it("趋势连续版本冲突后显示区块重试，保留当前经营卡片", async () => {
  let attempts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.includes("/performance/trend?")) {
        attempts++;
        return { ok: false, status: 409, json: async () => ({}) };
      }
      return { ok: true, json: async () => syntheticResponse(path) };
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const trend = screen.getByRole("region", { name: "经营趋势" });
  await waitFor(() => expect(attempts).toBe(2));
  expect(await within(trend).findByRole("button", { name: "重试读取经营趋势" })).toBeTruthy();
  expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy();
});

it("接口失败仍保留八卡及其他折叠，不创建无依据的推广异常", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
  await act(async () => {
    render(
      <MemoryRouter>
        <SalesPage />
      </MemoryRouter>,
    );
  });
  expect(screen.getByRole("heading", { name: "店铺销售看板" })).toBeTruthy();
  // 精确验证1秒、2秒退避；不让整页角色查询和机器负载挤占墙钟等待。
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(999);
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1999);
  });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByRole("button", { name: "重新读取本地数据" })).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(3);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(fetch).toHaveBeenCalledTimes(3);
  for (const title of [
    "销售金额",
    "店铺访客",
    "成交转化率",
    "客单价",
    "退款金额占比",
    "推广花费",
    "点击率",
    "ROI",
  ]) {
    expect(within(screen.getByTestId("sales-kpis")).getByText(title)).toBeTruthy();
  }
  expect(document.querySelectorAll("details").length).toBe(5);
  expect(screen.getByText("查看统计口径与历史补充").closest("details")!.open).toBe(false);
  expect(document.querySelector(".refund-coverage-note")).toBeNull();
  expect(
    within(screen.getByTestId("sales-kpis")).getByRole("button", { name: "计算明细" }),
  ).toBeTruthy();
  expect(screen.queryByLabelText("推广数据提示")).toBeNull();
  expect(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getAllByRole("columnheader"),
  ).toHaveLength(10);
  expect(
    within(document.querySelector<HTMLElement>("#regions")!).getAllByRole("columnheader"),
  ).toHaveLength(6);
  expect(
    within(document.querySelector<HTMLElement>("#product-regions")!).getAllByRole("columnheader"),
  ).toHaveLength(7);
  expect(screen.getByText("本月 GMV 目标进度")).toBeTruthy();
  expect(screen.queryByText(/开发样例/)).toBeNull();
});

it("单店切换与再次点击恢复不会改变全店贡献和底表分母", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((path: string) =>
        Promise.resolve({ ok: true, json: async () => syntheticResponse(path) }),
      ),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const kpis = screen.getByTestId("sales-kpis");
  await waitFor(() => expect(within(kpis).getByText("¥31")).toBeTruthy());
  const table = screen.getByRole("table", { name: "店铺完整数据" });
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.click(within(table).getByRole("button", { name: "户外店" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "户外店经营表现" })).toBeTruthy());
  await waitFor(() => expect(within(kpis).getByText("¥0")).toBeTruthy());
  expect(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByText("100.0%"),
  ).toBeTruthy();
  fireEvent.click(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByRole("button", {
      name: "户外店",
    }),
  );
  await waitFor(() => expect(within(kpis).getByText("¥31")).toBeTruthy());
});

it("连续409后停止自动重试", async () => {
  vi.useFakeTimers();
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      calls++;
      return Promise.resolve(
        path.includes("/filters")
          ? { ok: true, json: async () => syntheticResponse(path) }
          : { ok: false, status: 409 },
      );
    }),
  );
  // act等待初次读取及一次版本恢复的Promise和React提交，再查询页面角色。
  await act(async () => {
    render(
      <MemoryRouter>
        <SalesPage />
      </MemoryRouter>,
    );
  });
  expect(screen.getByRole("button", { name: "重新读取本地数据" })).toBeTruthy();
  expect(calls).toBe(6);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(calls).toBe(6);
  expect(screen.getByTestId("sales-kpis").textContent).not.toContain("¥31");
});

it("贡献区的小额不显示成零，真实零、缺记录与空金额分别表达", async () => {
  const items = [
    testShops[0],
    testShops[1],
    {
      ...testShops[0],
      shop_id: "empty",
      display_name: "空金额店",
      shop_revenue: null,
      sales_share: null,
      rank: null,
      field_states: { shop_revenue: "field_null" },
    },
    {
      ...testShops[0],
      shop_id: "missing",
      display_name: "无记录店",
      shop_revenue: null,
      sales_share: null,
      rank: null,
      field_states: { shop_revenue: "no_records" },
      coverage: { ...testMetrics.coverage, observed_days: 0 },
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          path.includes("/shops?")
            ? { meta: testMeta, data: { items, missing_shop_days: 1 } }
            : syntheticResponse(path),
      }),
    ),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const contribution = within(document.getElementById("shops")!);
  await waitFor(() => expect(contribution.getByText("¥30.50")).toBeTruthy());
  expect(contribution.getByText("¥0.00万")).toBeTruthy();
  expect(contribution.getByText("暂无销售记录")).toBeTruthy();
  expect(contribution.getByText("销售金额为空")).toBeTruthy();
  expect(contribution.queryByText(/目标入口不可用/)).toBeNull();
});

it("更新失败时用运营文案说明正在展示上次数据", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      const response = syntheticResponse(path);
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...response,
          meta: {
            ...response.meta,
            last_attempt_status: "failed",
            last_attempt_at: "2026-10-28T01:02:03Z",
          },
        }),
      });
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByText(/销售数据检查未成功，当前保留上次检查成功的数据/)).toBeTruthy(),
  );
  expect(screen.getByLabelText("数据覆盖说明").textContent).not.toMatch(
    /上游|入仓|最近尝试|时区未确认/,
  );
});

it("切换店铺后忽略已取消请求的晚到响应", async () => {
  let release = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      if (path.includes("shop_id=spihw"))
        return new Promise((resolve) => {
          release = () => resolve({ ok: true, json: async () => syntheticResponse(path) });
        });
      return Promise.resolve({ ok: true, json: async () => syntheticResponse(path) });
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const { fireEvent } = await import("@testing-library/react");
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
  fireEvent.click(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByRole("button", {
      name: "户外店",
    }),
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "显示全部店铺" })).toBeTruthy());
  // 切换期间保留原尺寸与带旧范围的数值，旧卡片不可操作；晚到响应仍不得覆盖新选择。
  const retained = screen.getByTestId("sales-kpis").closest("[aria-busy]");
  expect(retained?.getAttribute("aria-busy")).toBe("true");
  expect(retained?.hasAttribute("inert")).toBe(true);
  expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "显示全部店铺" }));
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
  release();
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
});

it("中心链接指定单日和店铺后显示实际范围且不先请求默认范围", async () => {
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      requested.push(path);
      return Promise.resolve({ ok: true, json: async () => syntheticResponse(path) });
    }),
  );
  render(
    <MemoryRouter
      initialEntries={["/sales?month=2026-09&from=2026-09-15&to=2026-09-15&shop=spihw#performance"]}
    >
      <SalesPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByRole("heading", { name: "户外店经营表现" })).toBeTruthy());
  expect(screen.getByText("经营数据：2026-09-15")).toBeTruthy();
  const facts = requested.filter((path) => path.includes("/performance?"));
  expect(facts).toHaveLength(1);
  expect(facts[0]).toContain("date_from=2026-09-15");
  expect(facts[0]).toContain("shop_id=spihw");
});

it.each([
  {
    label: "记录金额为空",
    days: 1,
    salesMissing: ["spigf:2026-10-27"],
    missing: [],
    empty: ["2026-10-26"],
    state: "observed",
    expected: "退款金额为空（有记录）：2026-10-26",
  },
  {
    label: "整行无记录",
    days: 1,
    salesMissing: ["spigf:2026-10-27"],
    missing: ["2026-10-26"],
    empty: [],
    state: "observed",
    expected: "无退款记录：2026-10-26",
  },
  {
    label: "真实零也是已提供金额",
    days: 2,
    salesMissing: [],
    missing: [],
    empty: [],
    state: "observed",
    expected: "退款金额均已提供",
  },
  {
    label: "退款尚不可用",
    days: 2,
    salesMissing: [],
    missing: [],
    empty: [],
    state: "not_connected",
    expected: "退款数据暂不可用",
  },
  {
    label: "无销售也独立展示退款为空",
    days: 0,
    salesMissing: ["spigf:2026-10-26", "spigf:2026-10-27"],
    missing: ["2026-10-27"],
    empty: ["2026-10-26"],
    state: "observed",
    expected: "退款金额为空（有记录）：2026-10-26",
  },
] as const)("覆盖提示区区分$label", ({ days, salesMissing, missing, empty, state, expected }) => {
  const result: SalesResult = {
    key: "coverage",
    meta: testMeta,
    performance: testMetrics,
    missingShopDays: salesMissing.length,
    shops: [
      {
        ...testShops[0],
        shop_revenue: days ? "0.00" : null,
        coverage: {
          ...testMetrics.coverage,
          observed_days: days,
          missing_dates: [...salesMissing],
        },
        refund_coverage: {
          state,
          missing_record_dates: [...missing],
          null_amount_dates: [...empty],
        },
      },
    ],
  };
  const { container } = render(<SalesCoverage result={result} error={null} />);
  expect(container.textContent).toContain(expected);
  expect(container.textContent).not.toMatch(/上游|入仓|完整日|已观察值|时区未确认|暂无法核对/);
  if (state === "observed" && empty.length === 0 && missing.length === 0)
    expect(container.textContent).not.toMatch(/无退款记录|退款金额为空/);
  if (state === "not_connected")
    expect(container.textContent).not.toMatch(/无退款记录|退款金额均已提供/);
  if (!days) expect(container.textContent).toContain("缺少 2 天销售记录");
});

it("外层仅提示概况，展开后六店分清金额为空和无记录，口径与时间在明细内", async () => {
  const result: SalesResult = {
    key: "coverage",
    meta: testMeta,
    performance: testMetrics,
    missingShopDays: 0,
    shops: syntheticAllShops.map((shop) => ({
      ...shop,
      coverage: { ...shop.coverage, observed_days: 5, missing_dates: [] },
      refund_coverage: {
        state: "observed",
        missing_record_dates: shop.shop_id === "spixb" ? ["2026-10-23"] : [],
        null_amount_dates:
          shop.shop_id === "spixb" ? ["2026-10-24", "2026-10-25", "2026-10-26"] : [],
      },
    })),
  };
  const { container } = render(<SalesCoverage result={result} error={null} />);
  const coverage = screen.getByLabelText("数据覆盖说明");
  const details = coverage.querySelector("details")!;
  expect(details.open).toBe(false);
  const overview = coverage.querySelector(":scope > p")!;
  expect(overview.textContent).toContain("销售记录齐全");
  expect(overview.textContent).toContain("1 家店有 3 条退款金额为空的记录");
  expect(overview.textContent).toContain("1 家店缺少 1 条每日退款记录");
  expect(overview.textContent).not.toMatch(/退款率|最近检查|2026-/);
  expect(
    within(coverage)
      .getByText(/最近检查时间：/)
      .closest("details"),
  ).toBe(details);
  expect(
    within(coverage)
      .getByText(/退款率仅统计/)
      .closest("details"),
  ).toBe(details);
  const { default: userEvent } = await import("@testing-library/user-event");
  await userEvent.click(within(coverage).getByText("查看各店铺销售与退款数据明细"));
  expect(details.open).toBe(true);
  const rows = within(coverage).getAllByRole("listitem");
  expect(rows).toHaveLength(6);
  for (const [i, row] of rows.entries()) {
    expect(within(row).getByText(syntheticAllShops[i].display_name)).toBeTruthy();
    expect(row.textContent).toContain("销售记录齐全，共 5 天");
    expect(row.closest("details")).toBe(details);
    if (syntheticAllShops[i].shop_id === "spixb") {
      expect(row.textContent).toContain(
        "退款金额为空（有记录）：2026-10-24、2026-10-25、2026-10-26",
      );
      expect(row.textContent).toContain("无退款记录：2026-10-23");
    } else expect(row.textContent).toContain("退款金额均已提供");
  }
  expect(container.textContent).not.toMatch(/不代表当天没有退款|不能仅凭该日期判断|5\/5/);
  expect(container.querySelectorAll(".sales-coverage-date")).toHaveLength(4);
});

it("日期范围变化后明细与范围内推广日期更新，加载清空旧数字", () => {
  const first: SalesResult = {
    key: "first",
    meta: { ...testMeta, last_success_at: "2026-10-05T00:01:00+08:00" },
    performance: testMetrics,
    missingShopDays: 1,
    shops: [
      {
        ...testShops[0],
        coverage: {
          ...testMetrics.coverage,
          observed_days: 3,
          window_start: "2026-10-01",
          window_end: "2026-10-04",
          latest_observed_date: "2026-10-03",
          missing_dates: ["spigf:2026-10-04"],
        },
        refund_coverage: {
          state: "observed",
          missing_record_dates: ["2026-10-04"],
          null_amount_dates: ["2026-10-02", "2026-10-03"],
        },
        promotion_channels: [
          {
            code: "shop_channel_product_promotion",
            latest_observed_date: "2026-10-03",
            spend: null,
            revenue: null,
            ctr: null,
            roi: null,
            missing_shop_days: 1,
            null_counts: {},
            source: {
              state: "observed",
              observed_through: "2026-10-27",
              source_updated_text: null,
            },
          },
        ],
      },
    ],
  };
  const { rerender, container } = render(<SalesCoverage result={first} error={null} />);
  expect(container.textContent).toContain("缺少 1 天销售记录：2026-10-04");
  expect(container.textContent).toContain("1 家店有 2 条退款金额为空的记录");
  expect(container.textContent).toContain("商品推广 2026-10-03");
  expect(container.textContent).not.toContain("2026-10-27");
  const second: SalesResult = {
    ...first,
    key: "second",
    missingShopDays: 0,
    meta: {
      ...first.meta,
      last_attempt_status: "failed",
      last_attempt_at: "2026-10-06T01:00:00+08:00",
    },
    shops: [
      {
        ...first.shops[0],
        coverage: {
          ...first.shops[0].coverage,
          observed_days: 1,
          window_start: "2026-10-01",
          window_end: "2026-10-01",
          latest_observed_date: "2026-10-01",
          missing_dates: [],
        },
        refund_coverage: { state: "observed", missing_record_dates: [], null_amount_dates: [] },
        promotion_channels: [
          { ...first.shops[0].promotion_channels![0], latest_observed_date: null },
        ],
      },
    ],
  };
  rerender(<SalesCoverage result={second} error={null} />);
  expect(container.textContent).toContain("销售记录齐全，共 1 天");
  expect(container.textContent).toContain("商品推广 暂无记录");
  expect(container.textContent).not.toMatch(/2026-10-03|2026-10-04|退款金额为空|无退款记录/);
  expect(screen.getByText(/最近检查时间：/).textContent).toContain(
    new Date(second.meta.last_attempt_at!).toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour12: false,
    }),
  );
  expect(container.textContent).toContain("销售数据检查未成功");
  const unavailable: SalesResult = {
    ...second,
    shops: second.shops.map((shop) => ({
      ...shop,
      promotion_channels: shop.promotion_channels?.map((channel) => ({
        ...channel,
        source: { ...channel.source, state: "not_connected" },
      })),
    })),
  };
  rerender(<SalesCoverage result={unavailable} error={null} />);
  expect(container.textContent).toContain("商品推广 暂不可用");
  expect(container.textContent).not.toContain("商品推广 暂无记录");
  rerender(<SalesCoverage result={null} error={null} />);
  expect(container.textContent).not.toContain("销售记录齐全");
  expect(container.textContent).not.toContain("商品推广");
});
