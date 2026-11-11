// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProductsPage } from "./ProductsPage";
import type { Board } from "./contracts";
import { readScope } from "./scope";
import { useOverviewPresentation } from "./useOverviewPresentation";

vi.mock("../../shared/layouts/DashboardLayout", () => ({
  DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  LineChart: () => null,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));

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
  vi.stubGlobal("scrollBy", vi.fn());
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function boardData(amount: string, count: string): Board {
  return {
    metrics: {
      revenue: {
        label: "交易金额",
        type: "currency",
        value: amount,
        previous: null,
        previous_period: null,
        yoy: null,
        mom: null,
        status: "observed",
        reason: null,
      },
    },
    comparison_series: [],
    comparison_mode: "categories",
    selection: { product_name: null, shop_name: null, operator: null, category: null, level: null },
    series: [],
    windows: [],
    health: { title: "交易额同比下降", tone: "neutral", detail: "同款比较" },
    risks: [],
    brief: {
      start: "2026-10-26",
      end: "2026-11-01",
      previous_start: "2026-10-19",
      categories: [{ category: "帽子", revenue: amount, growth: null }],
      products: [],
      risks: [],
      rising: [],
      actions: {
        generation_method: "rules",
        ai_status: "not_connected",
        status: "no_match",
        as_of_date: "2026-11-01",
        message: "继续观察",
        items: [],
        notices: [],
      },
    },
    funnel: [],
    observed_rows: count,
    product_count: count,
    scope: { start: "2026-10-02", end: "2026-11-01" },
    basis: "同款比较",
  };
}

function setup() {
  const pending: {
    params: URLSearchParams;
    signal?: AbortSignal;
    respond: (data?: Board) => void;
  }[] = [];
  const reply = (data: unknown) =>
    new Response(JSON.stringify({ data, meta: { data_version: "v1", request_id: "test" } }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  clients.push(client);
  client.setQueryData(["m1", "meta"], {
    data_version: "v1",
    date_from: "2026-09-01",
    date_to: "2026-11-01",
    observed_rows: "2",
    product_count: "2",
    success_at: null,
    complete_through: null,
    completeness: "unknown",
    profile_basis: "当前资料",
    latest_attempt: { status: "succeeded" },
    sources: [],
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const parsed = new URL(url, "http://localhost");
      const params = parsed.searchParams;
      if (parsed.pathname.endsWith("/overview"))
        return new Promise<Response>((resolve) =>
          pending.push({
            params,
            signal: init?.signal ?? undefined,
            respond: (data) =>
              resolve(
                data
                  ? reply(data)
                  : new Response(JSON.stringify({ error: { code: "REQUEST_FAILED" } }), {
                      status: 500,
                    }),
              ),
          }),
        );
      if (parsed.pathname.endsWith("/catalog"))
        return Promise.resolve(
          reply({
            shops: [{ value: "s", label: "店铺" }],
            items: ["帽子", "书包"].map((category, index) => ({
              value: `l${index}`,
              label: category,
              shop: "s",
              operator: null,
              category,
              level: null,
              search_text: category,
            })),
            total: 2,
          }),
        );
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: "NOT_CONNECTED" } }), { status: 503 }),
      );
    }),
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProductsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    pending,
    client,
    respond: async (index: number, data?: Board) => {
      await act(async () => pending[index].respond(data));
    },
  };
}
const amount = () => document.querySelector("#overview .metric strong")?.textContent;
const summary = () => document.querySelector("#overview .product-bar")?.textContent;
async function search(value: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "总览搜索商品" }), { target: { value } });
}

it("总览慢加载保留指标、上方榜单及旧范围，成功后一起替换且不插入跳动的加载块", async () => {
  const { pending, respond } = setup();
  await waitFor(() => expect(pending).toHaveLength(1));
  await respond(0, boardData("100", "2"));
  await waitFor(() => expect(amount()).toBe("¥100"));
  const metricNode = document.querySelector("#overview .metric");
  await search("帽子");
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(amount()).toBe("¥100");
  expect(summary()).toContain("2 个店铺商品链接");
  expect(
    within(document.getElementById("product-brief")!)
      .getByRole("button", { name: /帽子/ })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(document.querySelector("#overview .m1-loading")).toBeNull();
  expect(within(document.getElementById("overview")!).queryByRole("status")).toBeNull();
  expect(document.querySelector('#overview [aria-busy="true"]')).not.toBeNull();
  await respond(1, boardData("250", "1"));
  await waitFor(() => expect(amount()).toBe("¥250"));
  expect(document.querySelector("#overview .metric")).toBe(metricNode);
  expect(summary()).toContain("1 个店铺商品链接");
});

it("总览新筛选失败不清空页面或冒充新结果，重试成功后恢复", async () => {
  const { pending, respond } = setup();
  await waitFor(() => expect(pending).toHaveLength(1));
  await respond(0, boardData("100", "2"));
  await waitFor(() => expect(amount()).toBe("¥100"));
  await search("不存在");
  await waitFor(() => expect(pending).toHaveLength(2));
  await respond(1);
  await waitFor(() =>
    expect(within(document.getElementById("overview")!).getByRole("status").textContent).toMatch(
      /失败/,
    ),
  );
  expect(amount()).toBe("¥100");
  fireEvent.click(
    within(document.getElementById("overview")!).getByRole("button", { name: /重\s*试/ }),
  );
  await waitFor(() => expect(pending).toHaveLength(3));
  await respond(2, boardData("0", "0"));
  await waitFor(() => expect(amount()).toBe("¥0"));
  expect(summary()).toContain("0 个店铺商品链接");
});

it("连续切换取消旧请求，晚到结果不能覆盖最新条件，重置仍可继续操作", async () => {
  const { pending, respond } = setup();
  await waitFor(() => expect(pending).toHaveLength(1));
  await respond(0, boardData("100", "2"));
  await waitFor(() => expect(amount()).toBe("¥100"));
  await search("帽子");
  await waitFor(() => expect(pending).toHaveLength(2));
  await search("书包");
  await waitFor(() => expect(pending).toHaveLength(3));
  expect(pending[1].signal?.aborted).toBe(true);
  expect(amount()).toBe("¥100");
  await respond(2, boardData("300", "3"));
  await waitFor(() => expect(amount()).toBe("¥300"));
  await respond(1, boardData("200", "1"));
  expect(amount()).toBe("¥300");
  fireEvent.click(
    within(screen.getByLabelText("总览筛选")).getByRole("button", { name: /重\s*置/ }),
  );
  await waitFor(() => expect(amount()).toBe("¥100"));
});

it("上方结论变高时保持筛选框的屏幕位置，用户主动滚动时不拉回", async () => {
  let top = 200;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    return {
      top: this.classList.contains("m1-main-filters") ? top : 0,
      bottom: 450,
      left: 0,
      right: 500,
      width: 500,
      height: 250,
      x: 0,
      y: top,
      toJSON() {},
    };
  });
  const { pending, respond } = setup();
  await waitFor(() => expect(pending).toHaveLength(1));
  await respond(0, boardData("100", "2"));
  await waitFor(() => expect(amount()).toBe("¥100"));
  await search("帽子");
  await waitFor(() => expect(pending).toHaveLength(2));
  top = 240;
  await respond(1, boardData("200", "1"));
  await waitFor(() => expect(amount()).toBe("¥200"));
  expect(window.scrollBy).toHaveBeenCalledWith({ top: 40, behavior: "instant" });
  vi.mocked(window.scrollBy).mockClear();
  await search("书包");
  await waitFor(() => expect(pending).toHaveLength(3));
  fireEvent.wheel(window);
  top = 100;
  await respond(2, boardData("300", "3"));
  await waitFor(() => expect(amount()).toBe("¥300"));
  expect(window.scrollBy).not.toHaveBeenCalled();
});

it("保留展示时范围与数字绑定，版本变化立即撤下旧版结果", () => {
  const scope = readScope(
    new URLSearchParams("main_category=帽子"),
    "main",
    "2026-10-02",
    "2026-11-01",
  );
  const first = boardData("100", "2");
  const { result, rerender } = renderHook(
    ({ data, category, version }: { data: Board | undefined; category: string; version: string }) =>
      useOverviewPresentation(data, { ...scope, category }, version),
    { initialProps: { data: first as Board | undefined, category: "帽子", version: "v1" } },
  );
  rerender({ data: undefined, category: "书包", version: "v1" });
  expect(result.current.data?.metrics.revenue.value).toBe("100");
  expect(result.current.scope.category).toBe("帽子");
  rerender({ data: boardData("200", "3"), category: "书包", version: "v1" });
  expect(result.current.data?.metrics.revenue.value).toBe("200");
  expect(result.current.scope.category).toBe("书包");
  rerender({ data: undefined, category: "书包", version: "v2" });
  expect(result.current.data).toBeUndefined();
});

it.each(["focus", "arrow"])("请求中通过%s离开筛选或滚动页面后不拉回", async (action) => {
  let top = 200;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    return {
      top: this.classList.contains("m1-main-filters") ? top : 0,
      bottom: 450,
      left: 0,
      right: 500,
      width: 500,
      height: 250,
      x: 0,
      y: top,
      toJSON() {},
    };
  });
  const { pending, respond } = setup();
  await waitFor(() => expect(pending).toHaveLength(1));
  await respond(0, boardData("100", "2"));
  await waitFor(() => expect(amount()).toBe("¥100"));
  await search("帽子");
  await waitFor(() => expect(pending).toHaveLength(2));
  if (action === "focus") screen.getByRole("button", { name: /^交易金额/ }).focus();
  else fireEvent.keyDown(window, { key: "ArrowDown" });
  top = 100;
  await respond(1, boardData("200", "1"));
  await waitFor(() => expect(amount()).toBe("¥200"));
  expect(window.scrollBy).not.toHaveBeenCalled();
});
