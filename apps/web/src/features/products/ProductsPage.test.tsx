// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ProductsPage } from "./ProductsPage";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => computed(element));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function show(path = "/products?preview=1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ProductsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}
describe("商品整页交互与隔离", () => {
  it("通过共享看板布局展示顶部导航与页内目录", () => {
    show("/products?data=offline");
    const header = screen.getByRole("banner", { name: "看板导航" });
    expect(within(header).getByRole("link", { name: "店铺销售 ↗" }).getAttribute("href")).toBe(
      "/sales",
    );
    expect(screen.getByRole("navigation", { name: "本页目录" })).toBeTruthy();
  });

  it("目录按钮可往返收起展开且不影响正文", async () => {
    show("/products?data=offline");
    const toggle = screen.getByRole("button", { name: "收起本页目录" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "展开本页目录" }).getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(document.getElementById("product-days")).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "展开本页目录" }));
    expect(screen.getByRole("button", { name: "收起本页目录" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
  });

  it("页内目录的六个入口在未接入时也有可定位区块", async () => {
    show("/products?data=offline");
    const navigation = screen.getByRole("navigation", { name: "本页目录" });
    const links = within(navigation).getAllByRole("link");
    expect(links).toHaveLength(6);
    for (const link of links) {
      const href = link.getAttribute("href");
      expect(href?.startsWith("#")).toBe(true);
      const target = document.getElementById(href!.slice(1));
      expect(target).not.toBeNull();
      expect(target?.closest("main")).not.toBeNull();
    }
    await waitFor(() => expect(screen.getAllByText("数据尚未接入").length).toBeGreaterThan(3));
  });

  it("前置范围接口失败时明确报错，不发无版本经营查询或无限骨架", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => new Response("unavailable", { status: 503 }));
    show("/products");
    await waitFor(() =>
      expect(screen.getAllByText("页面数据范围加载失败").length).toBeGreaterThan(3),
    );
    expect(fetch.mock.calls.length).toBeGreaterThan(0);
    expect(fetch.mock.calls.every((call) => String(call[0]).includes("/filters?"))).toBe(true);
  });

  it("显式离线不请求经营接口，六块保留且不出现样例数字", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    show("/products?data=offline");
    await waitFor(() => expect(screen.getAllByText("数据尚未接入").length).toBeGreaterThan(3));
    expect(screen.getByRole("heading", { name: "经营核心结论" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "查看表现档位与放量建议" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "筛选单款数据" })).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByText("示例暖云抓绒衣")).toBeNull();
  });
  it("只搜索底表不改变总览与评分；明细条件更改后回第1页", async () => {
    const { client } = show("/products?preview=1&days.page=2");
    const detail = screen.getByRole("region", { name: "商品明细底表" });
    await waitFor(() => expect(within(detail).getByText("共 112 条结果")).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "商品明细搜索" }), {
      target: { value: "不存在的示例" },
    });
    await waitFor(() => expect(within(detail).getByText(/当前筛选下暂无匹配结果/)).toBeTruthy());
    expect(screen.getByRole("region", { name: "查看表现档位与放量建议" }).textContent).toContain(
      "匹配 6 / 6 款新品",
    );
    const overviewQueries = client.getQueryCache().findAll({ queryKey: ["products", "overview"] });
    expect(
      overviewQueries.every((query) => (query.queryKey[3] as { keyword: string }).keyword === ""),
    ).toBe(true);
    const latest = client
      .getQueryCache()
      .findAll({ queryKey: ["products", "days"] })
      .find((query) => (query.queryKey[3] as { keyword: string }).keyword === "不存在的示例");
    expect((latest?.queryKey[3] as { page: number }).page).toBe(1);
  });
  it("退出开发样例后撤下旧数字且不请求后端", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    show();
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "商品明细底表" }).textContent).toContain(
        "共 112 条结果",
      ),
    );
    await user.click(screen.getByRole("combobox", { name: "数据模式" }));
    await user.click(
      screen.getByText("未接入模式", { selector: ".ant-select-item-option-content" }),
    );
    await waitFor(() => expect(screen.getAllByText("数据尚未接入").length).toBeGreaterThan(3));
    expect(screen.queryByText("示例暖云抓绒衣")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("缺数不冒充同比零增长，评分pending不阻断经营总览", async () => {
    show("/products?preview=1&scenario=pending");
    await waitFor(() => expect(screen.getAllByText("结果尚未生成").length).toBe(2));
    expect(await screen.findByText("商品流量与支付表现")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "筛选单款数据" })).toBeTruthy();
  });
});
