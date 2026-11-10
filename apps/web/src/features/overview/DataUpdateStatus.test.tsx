// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { DataUpdateStatus } from "./DataUpdateStatus";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: () => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
    }),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const dataset = {
  code: "jst_shipped_sales",
  name: "已发货销售",
  version_id: "v1",
  date: "2026-11-02",
  received_at: "2026-11-03T03:00:00Z",
  last_success_at: "2026-11-03T03:05:00Z",
  last_attempt_at: "2026-11-03T03:00:00Z",
  last_finished_at: "2026-11-03T03:05:00Z",
  attempt_status: "succeeded",
  error_code: null,
  revision: null,
};
const row = {
  id: "jst_sales",
  name: "聚水潭销售 · 已发货 / 已取消",
  system: "warehouse",
  locator: "cubeappdata.ud_1_dsyyxskb",
  table_name: "ud_1_dsyyxskb",
  source_title: "加工表_电商运营销售明细表",
  source_url: "https://bi.kktree.cn/#/data_source?tableId=xihXkZKgJs10",
  pages: ["aftersales", "overview"],
  kind: "daily",
  datasets: [
    dataset,
    { ...dataset, code: "jst_cancelled_sales", name: "已取消销售", date: "2026-11-01" },
  ],
  platform_state: "available",
  upstream: {
    state: "unchanged",
    checked_at: "2026-11-03T03:06:00Z",
    observed_at: "2026-11-03T03:06:00Z",
    web_updated_at: "2026-11-03 07:43:37",
    web_checked_at: "2026-11-04T05:07:18+08:00",
    stale: false,
    observation: { date: "2026-11-02", basis: "recent_7_days_signal" },
    accepted_at: "2026-11-03T03:05:00Z",
    error_code: null,
    duration_ms: 50,
  },
};
const payload = () => ({
  data: {
    sources: [
      row,
      {
        ...row,
        id: "shop_daily",
        name: "店铺日经营",
        table_name: "ods_rpa_pdd_traffic_kanban_overview_f",
        pages: ["sales"],
        datasets: [
          { ...dataset, attempt_status: "failed", last_attempt_at: "2026-11-04T03:00:00Z" },
        ],
        platform_state: "failed",
        upstream: {
          ...row.upstream,
          stale: true,
          state: "failed",
          web_updated_at: null,
          web_checked_at: null,
        },
      },
    ],
    processing: [
      {
        ...dataset,
        code: "product_scores",
        name: "商品评分",
        state: "pending",
        generated_at: "2026-11-03T03:00:00Z",
        pages: ["products"],
      },
    ],
    monitor: { enabled: true, interval_seconds: 900 },
  },
  meta: { generated_at: "2026-11-03T03:06:00Z" },
});
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ConfigProvider theme={{ token: { motion: false } }}>
        <DataUpdateStatus />
      </ConfigProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

it("打开后只读专用状态，物理来源一行并可查看不同用途日期、按看板筛选", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(payload())));
  mount();
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const dialog = within(screen.getByRole("dialog"));
  await dialog.findByText("聚水潭销售 · 已发货 / 已取消");
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/data-status");
  expect(dialog.getAllByText("聚水潭销售 · 已发货 / 已取消")).toHaveLength(1);
  expect(dialog.getByText("检查已过期")).toBeTruthy();
  expect(dialog.getByText("最近同步失败")).toBeTruthy();
  expect(dialog.getByText("ud_1_dsyyxskb")).toBeTruthy();
  expect(
    dialog
      .getByRole("link", { name: "打开来源：聚水潭销售 · 已发货 / 已取消" })
      .getAttribute("href"),
  ).toBe("https://bi.kktree.cn/#/data_source?tableId=xihXkZKgJs10");
  expect(dialog.getByText("2026-11-03 07:43:37")).toBeTruthy();
  expect(dialog.getByText("尚无网页更新记录")).toBeTruthy();
  expect(dialog.getByText("检查失败")).toBeTruthy();
  expect(dialog.getByText(/本次尝试：2026\/11\/4 11:00:00/)).toBeTruthy();
  fireEvent.click(dialog.getAllByRole("button", { name: "展开来源详情" })[0]);
  expect(dialog.getByText("cubeappdata.ud_1_dsyyxskb")).toBeTruthy();
  expect(dialog.getByText("加工表_电商运营销售明细表")).toBeTruthy();
  expect(
    dialog.getByRole("button", { name: "复制完整来源：聚水潭销售 · 已发货 / 已取消" }),
  ).toBeTruthy();
  expect(dialog.getAllByText("2026-11-01").length).toBeGreaterThan(0);
  fireEvent.click(dialog.getByRole("radio", { name: "售后分析" }));
  expect(dialog.queryByText("店铺日经营")).toBeNull();
  expect(dialog.getByText("聚水潭销售 · 已发货 / 已取消")).toBeTruthy();
  fireEvent.click(dialog.getByRole("tab", { name: "平台处理" }));
  expect(dialog.getByText("商品评分")).toBeTruthy();
  expect(dialog.getByText("等待重算")).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("刷新失败保留上次状态并提示，不能把读取失败显示为正常", async () => {
  let fails = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    fails ? new Response("", { status: 503 }) : new Response(JSON.stringify(payload())),
  );
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("聚水潭销售 · 已发货 / 已取消");
  fails = true;
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await screen.findByText("状态读取失败", { selector: ".ant-alert-title" });
  expect(screen.getByText("聚水潭销售 · 已发货 / 已取消")).toBeTruthy();
});

it("同表某用途失败时显示该失败尝试时间，不能用另一用途后续成功时间替换", async () => {
  const data = payload();
  data.data.sources = [
    {
      ...row,
      platform_state: "failed",
      datasets: [
        { ...dataset, attempt_status: "failed", last_attempt_at: "2026-11-04T02:00:00Z" },
        {
          ...dataset,
          code: "jst_cancelled_sales",
          name: "已取消销售",
          last_attempt_at: "2026-11-04T02:05:00Z",
        },
      ],
    },
  ];
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("最近同步失败");
  expect(screen.getByText("本次尝试：2026/11/4 10:00:00")).toBeTruthy();
  expect(screen.queryByText("本次尝试：2026/11/4 10:05:00")).toBeNull();
});

it("固定时点展示数仓与飞书计划，仍仅刷新平台状态", async () => {
  const data = payload();
  const scheduled = {
    ...data,
    data: {
      ...data.data,
      monitor: {
        ...data.data.monitor,
        source_schedule_enabled: true,
        warehouse_times: ["08:20", "09:10", "10:10", "10:50", "13:10", "15:10", "16:10"],
        feishu_times: ["08:20", "10:10"],
      },
      sources: data.data.sources.map((source) => ({
        ...source,
        upstream: { ...source.upstream, next_check_at: "2026-11-04T09:10:00+08:00" },
      })),
    },
  };
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(scheduled)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("按计划读取数仓网页更新时间，有变化或接收失败时同步对应来源。");
  expect(screen.getByText(/数仓 08:20 \/ 09:10/)).toBeTruthy();
  expect(screen.getAllByText(/下次：2026\/11\/4 09:10:00/)).toHaveLength(2);
  expect(fetcher.mock.calls.every(([url]) => url === "/api/v1/data-status")).toBe(true);
});

it("网页自动读取与登录失效保留旧数据分别说明", async () => {
  const base = payload();
  const data = {
    ...base,
    data: {
      ...base.data,
      sources: [
        {
          ...row,
          upstream: {
            ...row.upstream,
            state: "failed",
            error_code: "WAREHOUSE_WEB_LOGIN_REQUIRED",
            observation: { date: "2026-11-02", basis: "warehouse_web_page" },
          },
        },
      ],
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("网页自动读取");
  fireEvent.click(screen.getByRole("button", { name: "展开来源详情" }));
  expect(
    screen.getByText("数仓网页登录已失效，保留上次更新时间和可用数据。重新登录后按计划继续检查。"),
  ).toBeTruthy();
});

it("未打开弹窗也提示全局同步失败，查看和关闭不会清除未恢复的提醒", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => new Response(JSON.stringify(payload())),
  );
  mount();
  expect(screen.queryByRole("dialog")).toBeNull();
  await screen.findByLabelText("数据更新异常");
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("最近同步失败");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("数据更新异常")).toBeTruthy();
});

it.each([
  ["数仓检查失败", { ...row, upstream: { ...row.upstream, state: "failed" } }],
  ["飞书同步失败", { ...row, system: "feishu", platform_state: "failed" }],
  ["天气同步失败", { ...row, system: "nmc", platform_state: "failed" }],
  [
    "同来源其他用途运行不能遮住失败",
    {
      ...row,
      platform_state: "syncing",
      datasets: [
        { ...dataset, attempt_status: "running" },
        { ...dataset, code: "other", attempt_status: "abandoned" },
      ],
    },
  ],
])("%s在入口统一提醒", async (_name, source) => {
  const data = payload();
  const response = { ...data, data: { ...data.data, sources: [source] } };
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => new Response(JSON.stringify(response)),
  );
  mount();
  await screen.findByLabelText("数据更新异常");
});

it("平台计算单独失败也提醒，恢复后自动清除", async () => {
  const data = payload();
  data.data.sources = [row];
  data.data.processing[0].state = "failed";
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  const { client } = mount();
  await screen.findByLabelText("数据更新异常");
  data.data.processing[0].state = "available";
  await client.invalidateQueries({ queryKey: ["data-update-status"] });
  await waitFor(() => expect(screen.queryByLabelText("数据更新异常")).toBeNull());
});

it("等待、运行、无记录不冒充失败，状态读取失败单独提醒且保留已知异常", async () => {
  const data = payload();
  data.data.sources = [
    row,
    { ...row, id: "pending", platform_state: "pending" },
    { ...row, id: "syncing", platform_state: "syncing" },
    { ...row, id: "empty", platform_state: "unavailable", datasets: [] },
  ];
  let fails = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    fails ? new Response("", { status: 503 }) : new Response(JSON.stringify(data)),
  );
  const { client } = mount();
  await waitFor(() => expect(client.getQueryState(["data-update-status"])?.status).toBe("success"));
  expect(screen.queryByLabelText("数据更新异常")).toBeNull();
  fails = true;
  await client.invalidateQueries({ queryKey: ["data-update-status"] });
  await screen.findByLabelText("状态读取失败");
  expect(screen.queryByLabelText("数据更新异常")).toBeNull();
  fails = false;
  data.data.sources = [{ ...row, platform_state: "failed" }];
  await client.invalidateQueries({ queryKey: ["data-update-status"] });
  await screen.findByLabelText("数据更新异常");
  fails = true;
  await client.invalidateQueries({ queryKey: ["data-update-status"] });
  await screen.findByLabelText("状态读取失败");
  expect(screen.getByLabelText("数据更新异常")).toBeTruthy();
});

it("异常标在对应行，顶部轻量定位能越过筛选并展开明细，恢复后标记消失", async () => {
  const data = payload();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  await screen.findByLabelText("数据更新异常");
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const dialog = within(screen.getByRole("dialog"));
  await dialog.findByLabelText("来源异常：店铺日经营");
  expect(dialog.queryByLabelText("来源异常：聚水潭销售 · 已发货 / 已取消")).toBeNull();
  expect(dialog.queryByText(/个来源更新异常/)).toBeNull();
  fireEvent.click(dialog.getByRole("radio", { name: "售后分析" }));
  fireEvent.click(dialog.getByRole("button", { name: "查看1项来源异常" }));
  expect(dialog.getByLabelText("来源异常：店铺日经营")).toBeTruthy();
  expect(
    dialog.getByText("失败原因尚未确认。已有成功数据仍可使用，请以数据日期为准。"),
  ).toBeTruthy();
  expect(dialog.getByRole("button", { name: "收起来源详情" })).toBeTruthy();
  data.data.sources = [row];
  fireEvent.click(dialog.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(dialog.queryByLabelText("来源异常：店铺日经营")).toBeNull());
  expect(screen.queryByLabelText("数据更新异常")).toBeNull();
});
it("平台处理异常同样标行并可定位解释，不改变等待重算的判断", async () => {
  const data = payload();
  data.data.sources = [row];
  data.data.processing[0].state = "failed";
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByRole("button", { name: "查看1项处理异常" });
  fireEvent.click(screen.getByRole("button", { name: "查看1项处理异常" }));
  expect(screen.getByLabelText("处理异常：商品评分")).toBeTruthy();
  expect(screen.getByText(/影响页面：商品数据/)).toBeTruthy();
});

it("历史输入待恢复时保留可用数据，不误报上游检查失败", async () => {
  const base = payload();
  const data = {
    ...base,
    data: {
      ...base.data,
      sources: [
        {
          ...row,
          platform_state: "pending",
          datasets: [
            { ...dataset, attempt_status: "deferred", error_code: "DATASET_RESTORE_REQUIRED" },
          ],
          upstream: { ...row.upstream, state: "changed", error_code: "DATASET_RESTORE_REQUIRED" },
        },
      ],
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("聚水潭销售 · 已发货 / 已取消");
  fireEvent.click(screen.getByRole("button", { name: "展开来源详情" }));
  expect(
    screen.getByText("继续展示最近可用数据；本次更新需要技术人员恢复历史输入后继续。"),
  ).toBeTruthy();
  expect(screen.queryByText("本轮上游检查失败，保留上次观测。")).toBeNull();
});

it("周对账失败单独提醒，保留数据可用状态和下一次重试时间", async () => {
  const data = payload();
  data.data.sources = [
    {
      ...row,
      id: "shop_daily",
      name: "店铺日经营",
      reconciliation: {
        status: "failed",
        cycle_at: "2026-11-08T09:00:00Z",
        last_attempt_at: "2026-11-08T09:00:00Z",
        last_finished_at: "2026-11-08T09:02:00Z",
        last_success_at: null,
        next_retry_at: "2026-11-08T09:07:00Z",
        next_scheduled_at: null,
        error_code: "SYNC_FAILED",
      },
    } as (typeof data.data.sources)[number],
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  await screen.findByLabelText("数据更新异常");
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const dialog = within(screen.getByRole("dialog"));
  expect(await dialog.findByText("数据可用")).toBeTruthy();
  expect(dialog.getByText("周对账未完成")).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "查看1项来源异常" }));
  expect(dialog.getByText("下一次重试")).toBeTruthy();
  expect(dialog.getByText("2026/11/8 17:07:00")).toBeTruthy();
  expect(dialog.queryByText(/平台接收未成功/)).toBeNull();
});
