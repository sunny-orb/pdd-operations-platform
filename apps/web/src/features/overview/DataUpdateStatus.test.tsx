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

it("RPA物理表显示真实写入时间和三类本地用途，不伪造网页入口或日缺口", async () => {
  const data = payload();
  data.data.sources = [
    {
      ...row,
      id: "aftersales_rpa",
      name: "RPA售后报表",
      system: "rpa",
      kind: "period",
      locator: "public.aftersales_reports_rpa",
      table_name: "aftersales_reports_rpa",
      source_title: "RPA售后报表",
      source_url: null as unknown as string,
      datasets: [
        { ...dataset, code: "rpa_returns", name: "售后退货", date: "2026-11-16" },
        { ...dataset, code: "rpa_shipped_sales", name: "已发货销售", date: "2026-11-16" },
        { ...dataset, code: "rpa_cancelled_sales", name: "已取消销售", date: "2026-11-16" },
      ],
      upstream: {
        ...row.upstream,
        state: "observed",
        web_updated_at: null as unknown as string,
        web_checked_at: null as unknown as string,
        observation: {
          date: "2026-11-16",
          basis: "rpa_table",
          source_updated: "2026-11-16T00:55:39Z",
          count: 55462,
        } as typeof row.upstream.observation,
      },
    },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const dialog = within(screen.getByRole("dialog"));
  await dialog.findByText("RPA售后报表");
  expect(dialog.getByText("2026/11/16 08:55:39")).toBeTruthy();
  expect(dialog.getByText("数据库写入时间")).toBeTruthy();
  expect(dialog.getByText("已检查")).toBeTruthy();
  expect(dialog.getAllByText("2026-11-16")).toHaveLength(3);
  expect(dialog.queryByRole("link", { name: "打开来源：RPA售后报表" })).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "展开来源详情" }));
  expect(dialog.getByText("public.aftersales_reports_rpa")).toBeTruthy();
  expect(dialog.getByText("55,462")).toBeTruthy();
  expect(dialog.queryByText("打开上游来源")).toBeNull();
});

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
  fireEvent.click(dialog.getByRole("button", { name: "关闭数据更新状态" }));
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
  fireEvent.click(screen.getByRole("button", { name: "关闭数据更新状态" }));
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
  fireEvent.click(dialog.getByRole("button", { name: "同步失败 1 项" }));
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
  await screen.findByRole("button", { name: "处理失败 1 项" });
  fireEvent.click(screen.getByRole("button", { name: "处理失败 1 项" }));
  expect(screen.getByLabelText("处理异常：商品评分")).toBeTruthy();
  expect(screen.getByText(/影响页面：商品数据/)).toBeTruthy();
});

it("无记录只作数据说明，不计故障或红点，可独立定位店铺日期", async () => {
  const data = payload();
  const history = {
    state: "gaps",
    window_start: "2026-10-11",
    window_end: "2026-11-09",
    checked_through: "2026-11-09",
    checked_at: "2026-11-10T02:00:00Z",
    missing_count: 2,
    missing_date_count: 2,
    groups: [{ shop_id: "spigf", shop_name: "官方店", dates: ["2026-10-21", "2026-11-09"] }],
  };
  data.data.sources = [{ ...row, history } as typeof row];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: /数据更新状态/ }));
  fireEvent.click(await screen.findByRole("button", { name: "部分日期无记录：1 个来源" }));
  expect(await screen.findByText("部分日期无记录")).toBeTruthy();
  expect(screen.getByText(/2026-10-11 至 2026-11-09/)).toBeTruthy();
  expect(screen.getByText(/官方店：/)).toBeTruthy();
  expect(screen.queryByText(/平台接收未成功/)).toBeNull();
  expect(screen.queryByLabelText("数据更新异常")).toBeNull();
  expect(screen.queryByLabelText(`来源异常：${row.name}`)).toBeNull();
  expect(screen.queryByText(/共.*项异常/)).toBeNull();
  expect(screen.getByLabelText(`记录缺失：${row.name}`)).toBeTruthy();
  expect(document.querySelector(".m6-update-badge .ant-badge-dot")).toBeNull();
});

it("新变化等待接收与旧业务日期不算失败，实际检查失败单列而不冒充同步失败", async () => {
  const data = payload();
  data.data.sources = [
    {
      ...row,
      id: "waiting",
      platform_state: "pending",
      upstream: { ...row.upstream, state: "changed" },
      datasets: [{ ...dataset, date: "2026-10-20" }],
    },
    { ...row, id: "checking", upstream: { ...row.upstream, state: "failed" } },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  fireEvent.click(await screen.findByRole("button", { name: "检查失败 1 项" }));
  expect(screen.queryByRole("button", { name: /同步失败 \d/ })).toBeNull();
  expect(screen.getByText(/上游检查未成功/)).toBeTruthy();
  expect(screen.queryByText(/平台接收未成功/)).toBeNull();
  expect(screen.queryByText(/并发限制/)).toBeNull();
});

it("更新延迟独立黄色提醒并定位依据，不改同步成功或把无记录当失败", async () => {
  const data = payload();
  data.data.sources = [
    {
      ...row,
      timeliness: {
        state: "delayed",
        baseline_updated_at: "2026-11-09 09:30:00",
        expected_by: "2026-11-10 10:30:00",
        observed_at: "2026-11-10T02:50:00Z",
      },
    } as typeof row,
    { ...row, id: "unknown", name: "缺少时间依据", timeliness: { state: "unknown" } } as typeof row,
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  await screen.findByLabelText("上游更新延迟");
  expect(document.querySelector(".m6-update-badge-delayed .ant-badge-dot")).toBeTruthy();
  expect(screen.queryByLabelText("数据更新异常")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  fireEvent.click(await screen.findByRole("button", { name: "更新延迟 1 项" }));
  expect(screen.getByLabelText(`更新延迟：${row.name}`)).toBeTruthy();
  expect(screen.getByText(/昨日上游标注：2026-11-09 09:30:00/)).toBeTruthy();
  expect(screen.getByText(/今日判断期限：2026-11-10 10:30:00/)).toBeTruthy();
  expect(screen.queryByText("缺少时间依据")).toBeNull();
  expect(screen.queryByRole("button", { name: /同步失败/ })).toBeNull();
  expect(screen.queryByLabelText(`来源异常：${row.name}`)).toBeNull();
  data.data.sources = [{ ...row, timeliness: { state: "updated" } } as typeof row];
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByLabelText("上游更新延迟")).toBeNull());
  expect(screen.queryByLabelText(`更新延迟：${row.name}`)).toBeNull();
  expect(screen.queryByRole("button", { name: "更新延迟 1 项" })).toBeNull();
  await waitFor(() => expect(document.querySelector(".m6-update-badge .ant-badge-dot")).toBeNull());
});

it("无依据和超时未核对保持中性，不显示延迟报警", async () => {
  const data = payload();
  data.data.sources = [
    { ...row, timeliness: { state: "unknown" } } as typeof row,
    {
      ...row,
      id: "waiting",
      timeliness: { state: "awaiting_check", expected_by: "2026-11-10 10:30:00" },
    } as typeof row,
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  expect(await screen.findByText("暂无可靠时间依据")).toBeTruthy();
  expect(screen.getByText("等待核对")).toBeTruthy();
  expect(screen.queryByLabelText("上游更新延迟")).toBeNull();
  expect(screen.queryByRole("button", { name: /更新延迟 \d/ })).toBeNull();
  expect(screen.queryByRole("img", { name: /^更新延迟：/ })).toBeNull();
});

it("更新说明默认收起，展开后仍能查看真实计划和时效规则", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => new Response(JSON.stringify(payload())),
  );
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("聚水潭销售 · 已发货 / 已取消");
  const disclosure = screen.getByText("更新说明").closest("details");
  expect(disclosure).toBeTruthy();
  expect(disclosure?.open).toBe(false);
  fireEvent.click(screen.getByText("更新说明"));
  expect(disclosure?.open).toBe(true);
  expect(within(disclosure!).getByText(/当前每 15 分钟检查一次/)).toBeTruthy();
  expect(within(disclosure!).getByText(/昨天更新时间/)).toBeTruthy();
});

it("读取未完成和筛选展开后均可关闭，晚响应不能重新打开弹窗", async () => {
  let complete!: (response: Response) => void;
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "关闭数据更新状态" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  complete(new Response(JSON.stringify(payload())));
  await screen.findByLabelText("数据更新异常");
  expect(screen.queryByRole("dialog")).toBeNull();
  fetcher.mockImplementation(async () => new Response(JSON.stringify(payload())));
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  fireEvent.click(await screen.findByRole("button", { name: "同步失败 1 项" }));
  expect(screen.getByRole("button", { name: "收起来源详情" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "关闭数据更新状态" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("刷新进行中可关闭，重新打开后Escape也可关闭", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(payload())));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("聚水潭销售 · 已发货 / 已取消");
  let complete!: (response: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(complete).toBeTypeOf("function"));
  fireEvent.click(screen.getByRole("button", { name: "关闭数据更新状态" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  complete(new Response(JSON.stringify(payload())));
  fetcher.mockImplementation(async () => new Response(JSON.stringify(payload())));
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  fireEvent.click(screen.getByRole("tab", { name: "平台处理" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape", keyCode: 27 });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("故障、延迟与缺记录并存时入口红点优先，恢复后降为黄点，只有缺记录时熄灭", async () => {
  const data = payload();
  const history = {
    state: "gaps",
    missing_count: 1,
    missing_date_count: 1,
    groups: [{ shop_id: "one", shop_name: "官方店", dates: ["2026-11-09"] }],
  };
  const delayedSource = {
    ...row,
    id: "delayed-source",
    name: "另一延迟来源",
    timeliness: { state: "delayed" },
  } as typeof row;
  data.data.sources = [{ ...row, platform_state: "failed", history } as typeof row, delayedSource];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  await screen.findByLabelText("数据更新异常");
  expect(document.querySelector(".m6-update-badge .ant-badge-dot")).toBeTruthy();
  expect(document.querySelector(".m6-update-badge-delayed")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  expect(await screen.findByLabelText(`来源异常：${row.name}`)).toBeTruthy();
  expect(screen.getByLabelText(`记录缺失：${row.name}`)).toBeTruthy();
  expect(screen.getByLabelText("更新延迟：另一延迟来源")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "部分日期无记录：1 个来源" }));
  expect(screen.getByText(/官方店：/)).toBeTruthy();
  data.data.sources = [{ ...row, history } as typeof row, delayedSource];
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByLabelText("数据更新异常")).toBeNull());
  expect(screen.queryByLabelText(`来源异常：${row.name}`)).toBeNull();
  expect(screen.getByLabelText(`记录缺失：${row.name}`)).toBeTruthy();
  expect(document.querySelector(".m6-update-badge-delayed .ant-badge-dot")).toBeTruthy();
  data.data.sources = [{ ...row, history } as typeof row];
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(document.querySelector(".m6-update-badge .ant-badge-dot")).toBeNull());
  expect(screen.getByLabelText(`记录缺失：${row.name}`)).toBeTruthy();
  data.data.sources = [
    {
      ...row,
      history: {
        ...history,
        state: "observed",
        missing_count: 0,
        missing_date_count: 0,
        groups: [],
      },
    } as typeof row,
  ];
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByLabelText(`记录缺失：${row.name}`)).toBeNull());
  expect(screen.queryByRole("button", { name: "部分日期无记录：1 个来源" })).toBeNull();
});

it("旧检查的缺记录灰点注明上次结果，未知及未发现缺口不标灰点", async () => {
  const data = payload();
  data.data.sources = [
    { ...row, history: { state: "stale", missing_count: 2, missing_date_count: 1 } } as typeof row,
    { ...row, id: "pending", name: "尚未检查", history: { state: "pending" } } as typeof row,
    {
      ...row,
      id: "observed",
      name: "已有记录",
      history: { state: "observed", missing_count: 0 },
    } as typeof row,
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: /数据更新状态/ }));
  expect(await screen.findByLabelText(`上次检查记录缺失：${row.name}`)).toBeTruthy();
  expect(screen.getByText(/上次结果/)).toBeTruthy();
  expect(screen.queryByLabelText("记录缺失：尚未检查")).toBeNull();
  expect(screen.queryByLabelText("记录缺失：已有记录")).toBeNull();
  expect(document.querySelector(".m6-update-badge .ant-badge-dot")).toBeNull();
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

it.each([
  ["ARCHIVE_CAPACITY", "归档空间不足，未完成本轮接收。"],
  ["INCREMENTAL_BASELINE_MISMATCH", "接收基线未通过一致性校验，未完成本轮接收。"],
  ["WAREHOUSE_CONCURRENCY_LIMIT", "数仓并发受限，未完成本轮接收。"],
])("接收失败类别 %s 保留已接收数据，不误报检查失败", async (code, reason) => {
  const base = payload();
  const data = {
    ...base,
    data: {
      ...base.data,
      sources: [
        {
          ...row,
          platform_state: "failed",
          datasets: [{ ...dataset, attempt_status: "failed", error_code: code }],
          upstream: { ...row.upstream, state: "unchanged", error_code: code },
        },
      ],
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  await screen.findByText("聚水潭销售 · 已发货 / 已取消");
  expect(screen.getByRole("button", { name: "同步失败 1 项" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /检查失败/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "展开来源详情" }));
  expect(screen.getByText(`${reason}保留最近可用数据。`)).toBeTruthy();
  expect(screen.getByText(`${reason}已有成功数据仍可使用，请以数据日期为准。`)).toBeTruthy();
  expect(screen.queryByText("本轮上游检查失败，保留上次观测。")).toBeNull();
  expect(screen.getByText("数据日期：2026-11-02")).toBeTruthy();
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
  fireEvent.click(dialog.getByRole("button", { name: "周对账失败 1 项" }));
  expect(dialog.getByText("近7日记录数、业务日期及来源时间；历史修订按每周对账核对")).toBeTruthy();
  expect(dialog.getByText("下一次重试")).toBeTruthy();
  expect(dialog.getByText("2026/11/8 17:07:00")).toBeTruthy();
  expect(dialog.queryByText(/平台接收未成功/)).toBeNull();
  expect(dialog.queryByRole("button", { name: /^(同步|检查)失败/ })).toBeNull();
});

it("更新说明保留普通16:10检查与独立的周日对账和重试计划", async () => {
  const data = payload();
  Object.assign(data.data.monitor, {
    source_schedule_enabled: true,
    warehouse_times: ["15:10", "16:10"],
    feishu_times: ["08:20", "10:10"],
    reconciliation_time: "17:00",
    reconciliation_retry_seconds: 300,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data)));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const disclosure = (await screen.findByText("更新说明")).closest("details")!;
  expect(disclosure.open).toBe(false);
  fireEvent.click(within(disclosure).getByText("更新说明"));
  expect(await within(disclosure).findByText(/16:10 为普通检查/)).toBeTruthy();
  expect(within(disclosure).getByText(/每周日 17:00 对账/)).toBeTruthy();
  expect(within(disclosure).getByText(/失败结束后 5 分钟重试/)).toBeTruthy();
});
