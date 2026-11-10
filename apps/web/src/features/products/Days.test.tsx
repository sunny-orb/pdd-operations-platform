// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
// 发布信号本身在shared及overview组合测试验证，本文件只控制领域响应顺序。
vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
vi.mock("../../shared/api/usePublishedDomainRefresh", () => ({
  usePublishedDomainRefresh: () => {},
}));
import { Days } from "./Days";
import { useProducts } from "./data";
import type { DayPage } from "./contracts";

const clients: QueryClient[] = [];
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
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

function page(number: number, size = 25): DayPage {
  return {
    items: [
      {
        listing_id: "shop/product",
        shop_id: "shop",
        shop_name: "测试店",
        product_id: "product",
        product_name: `第${number}页商品`,
        operator: null,
        category: null,
        level: null,
        field_status: {},
        date: "2026-11-01",
        visitors: "10",
        revenue: "0",
        conversion: "0",
        yoy: null,
      },
    ],
    total: 61,
    page: number,
    size,
    scope: { start: "2026-10-02", end: "2026-11-01" },
  };
}

function setup(initialEntry = "/") {
  const pending: { url: URL; resolve: (value: unknown) => void }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/catalog"))
        return Promise.resolve({
          ok: true,
          json: async () => ({
            data: { shops: [], items: [], total: 0 },
            meta: { data_version: "v1", request_id: "t" },
          }),
        });
      return new Promise((resolve) =>
        pending.push({ url: new URL(url, "http://localhost"), resolve }),
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  clients.push(client);
  client.setQueryData(["m1", "meta"], {
    data_version: "v1",
    date_from: "2026-09-01",
    date_to: "2026-11-01",
  });
  function App() {
    const state = useProducts();
    return (
      <>
        <output aria-label="总览范围">{state.scope("main").keyword || "全部"}</output>
        <Days state={state} scope={state.scope("detail")} />
      </>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const reply = async (index: number, data?: DayPage) => {
    await waitFor(() => expect(pending[index]).toBeTruthy());
    await act(async () =>
      pending[index].resolve({
        ok: !!data,
        status: data ? 200 : 500,
        json: async () =>
          data
            ? { data, meta: { data_version: "v1", request_id: "t" } }
            : { error: { code: "REQUEST_FAILED" } },
      }),
    );
  };
  return { pending, reply, client };
}

it("翻页等待和失败保留当前页，成功才切页，重试不丢当前记录", async () => {
  const { reply } = setup();
  await reply(0, page(1));
  await screen.findByText("第1页商品");
  fireEvent.click(screen.getByRole("button", { name: /^(right|下一页)$/ }));
  expect(screen.queryByText("第1页商品")).not.toBeNull();
  expect(screen.getByText("第 1 / 3 页")).toBeTruthy();
  expect(screen.getByRole("button", { name: "下一页" }).hasAttribute("disabled")).toBe(true);
  await reply(1);
  await screen.findByText("第2页加载失败，仍显示第1页。");
  expect(screen.getByText("第1页商品")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await reply(2, page(2));
  await screen.findByText("第2页商品");
  expect(screen.queryByText("第1页商品")).toBeNull();
  expect(screen.getByText("第 2 / 3 页")).toBeTruthy();
});

it("改变每页数量回第一页，搜索和版本变化不显示其他范围的旧记录", async () => {
  const { reply, pending, client } = setup();
  await reply(0, page(1));
  await screen.findByText("第1页商品");
  fireEvent.change(screen.getByRole("combobox", { name: "明细每页显示条数" }), {
    target: { value: "50" },
  });
  await waitFor(() => expect(pending[1]?.url.searchParams.get("page_size")).toBe("50"));
  expect(pending[1].url.searchParams.get("page")).toBe("1");
  expect(screen.getByText("第1页商品")).toBeTruthy();
  await reply(1, page(1, 50));
  await screen.findByText("第 1 / 2 页");
  fireEvent.change(screen.getByRole("textbox", { name: "明细搜索商品" }), {
    target: { value: "不存在" },
  });
  expect(screen.queryByText("第1页商品")).toBeNull();
  expect(screen.getByLabelText("总览范围").textContent).toBe("全部");
  await waitFor(() => expect(pending[2]?.url.searchParams.get("keyword")).toBe("不存在"));
  await reply(2, { ...page(1, 50), items: [], total: 0 });
  await screen.findByText("当前筛选范围暂无记录");
  act(() =>
    client.setQueryData(["m1", "meta"], {
      data_version: "v2",
      date_from: "2026-09-01",
      date_to: "2026-11-01",
    }),
  );
  await waitFor(() => expect(pending[3]?.url.searchParams.get("data_version")).toBe("v2"));
  expect(screen.queryByText("当前筛选范围暂无记录")).toBeNull();
});

it("真实零照常展示，不能计算的转化和同比不伪装为零", async () => {
  const { reply } = setup();
  const data = page(1);
  data.items.push({
    ...data.items[0],
    listing_id: "shop/empty",
    product_name: "空值商品",
    product_id: "empty",
    visitors: null,
    revenue: null,
    conversion: null,
  });
  await reply(0, data);
  const empty = (await screen.findByText("空值商品")).closest("tr")!;
  expect(within(empty).getAllByText("—")).toHaveLength(4);
  const zero = screen.getByText("第1页商品").closest("tr")!;
  expect(within(zero).getByText("¥0")).toBeTruthy();
  expect(within(zero).getByText("0.00%")).toBeTruthy();
});

it("日期反向时说明如何修正，恢复有效日期后正常查询", async () => {
  const { pending, reply } = setup("/?detail_from=2026-11-01&detail_to=2026-10-02");
  expect(await screen.findByText("结束日期不能早于开始日期，请修改后再查询。")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  expect(pending).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: /^重\s*置$/ }));
  await reply(0, page(1));
  await screen.findByText("第1页商品");
  expect(screen.queryByText("请调整筛选日期")).toBeNull();
});

it("晚到旧页不覆盖新搜索结果，回看缓存页时内容与页码同时恢复", async () => {
  const { reply } = setup();
  await reply(0, page(1));
  await screen.findByText("第1页商品");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.change(screen.getByRole("textbox", { name: "明细搜索商品" }), {
    target: { value: "新范围" },
  });
  await reply(2, { ...page(1), items: [], total: 0 });
  await screen.findByText("当前筛选范围暂无记录");
  await reply(1, page(2));
  expect(screen.queryByText("第2页商品")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^重\s*置$/ }));
  await screen.findByText("第1页商品");
  expect(screen.getByText("第 1 / 3 页")).toBeTruthy();
});
