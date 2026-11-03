// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { Details } from "./Details";
import { useResource, type PromotionState } from "./data";
import { days } from "./contracts";
import { readScope } from "./state";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const row = (page: number, source_label = "数仓") => ({
  date: `2026-10-${page === 1 ? "30" : "26"}`,
  shop_id: source_label,
  shop_name: `测试店${page}`,
  channel: "product_promotion",
  channel_label: "商品推广",
  source: "cubeappdata.long_table",
  source_label,
  spend: "12.34",
  revenue: "30",
  net_revenue: "25",
  deals: "2",
  impressions: "100",
  clicks: "10",
  roi: "2.43",
  ctr: "0.1",
  cvr: "0.2",
});
const response = (page: number) => ({
  ok: true,
  status: 200,
  json: async () => ({
    data: {
      items: [row(page), row(page, "飞书"), row(page, "人工确认")],
      total: 80,
      page,
      size: 25,
      total_pages: 4,
      row_grain: "shop_channel_day",
    },
    meta: { data_version: "v1", request_id: "test" },
  }),
});
function setup() {
  const pending: ((value: ReturnType<typeof response>) => void)[] = [];
  const fetch = vi.fn((url: string) => {
    const q = new URL(url, "http://localhost").searchParams;
    if (
      q.get("page") === "1" &&
      !q.get("shop_id") &&
      !q.get("channel") &&
      q.get("date_from") === "2026-10-01" &&
      q.get("data_version") === "v1"
    )
      return Promise.resolve(response(1));
    return new Promise<ReturnType<typeof response>>((resolve) => pending.push(resolve));
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Harness() {
    const [scope, setScope] = useState(
      readScope(new URLSearchParams(), "store", "2026-10-01", "2026-10-30"),
    );
    const [version, setVersion] = useState("v1");
    const resource = useResource("days", days, "store", scope, version, null);
    const change = (patch: Partial<typeof scope>) => setScope((s) => ({ ...s, page: 1, ...patch }));
    return (
      <>
        <button onClick={() => change({ shop: "other" })}>换店</button>
        <button onClick={() => change({ channel: "star_shop" })}>换渠道</button>
        <button onClick={() => change({ from: "2026-10-02" })}>换日期</button>
        <button onClick={() => setVersion("v2")}>新版本</button>
        <Details
          state={{ mode: "store", scope, version, days: resource, change } as PromotionState}
        />
      </>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  return { fetch, pending };
}

it("慢翻页保留行、条数和页码；防止重复点击，成功后切换，返回已看页不重复请求", async () => {
  const { fetch, pending } = setup();
  await screen.findByText("共 80 条记录");
  const scroller = screen.getByRole("table").parentElement!;
  scroller.scrollTop = 100;
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(screen.getAllByText("测试店1")).toHaveLength(3);
  expect(screen.getByText("第 1 / 4 页")).toBeTruthy();
  expect(scroller.scrollTop).toBe(100);
  expect((screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(pending).toHaveLength(1);
  await act(async () => pending[0](response(2)));
  await screen.findByText("第 2 / 4 页");
  expect(scroller.scrollTop).toBe(0);
  expect(screen.queryByText("测试店1")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  await screen.findByText("第 1 / 4 页");
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("翻页失败保留上一页，提示失败且重试成功，不显示无记录", async () => {
  const { pending } = setup();
  await screen.findByText("共 80 条记录");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () =>
    pending[0]({
      ok: false,
      status: 503,
      json: async () => ({ error: { code: "FAILED" } }),
    } as unknown as ReturnType<typeof response>),
  );
  await screen.findByText("第2页加载失败，仍显示第1页。");
  expect(screen.getAllByText("测试店1")).toHaveLength(3);
  expect(screen.queryByText(/暂无记录/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1](response(2)));
  await screen.findByText("第 2 / 4 页");
  expect(screen.queryByText(/加载失败/)).toBeNull();
});

it.each(["换店", "换渠道", "换日期", "新版本"])("%s不沿用旧范围或旧版本的数据", async (label) => {
  setup();
  await screen.findByText("共 80 条记录");
  fireEvent.click(screen.getByRole("button", { name: label }));
  expect(screen.queryByText("测试店1")).toBeNull();
  expect(screen.getByText("正在读取明细…")).toBeTruthy();
});

it("只显示短来源，不重复标题，保留原金额", async () => {
  setup();
  await screen.findByText("数仓");
  expect(screen.getByText("飞书")).toBeTruthy();
  expect(screen.getByText("人工确认")).toBeTruthy();
  expect(screen.queryByText("cubeappdata.long_table")).toBeNull();
  expect(screen.queryByText("推广明细")).toBeNull();
  expect(screen.getAllByText("¥12.34")).toHaveLength(3);
});

it("新筛选读取失败不显示无记录，也不保留上个店铺的明细", async () => {
  const { pending } = setup();
  await screen.findByText("共 80 条记录");
  fireEvent.click(screen.getByRole("button", { name: "换店" }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () =>
    pending[0]({
      ok: false,
      status: 503,
      json: async () => ({ error: { code: "FAILED" } }),
    } as unknown as ReturnType<typeof response>),
  );
  await screen.findByText("明细暂时无法加载，请重试。");
  expect(screen.queryByText("测试店1")).toBeNull();
  expect(screen.queryByText(/暂无记录/)).toBeNull();
});
