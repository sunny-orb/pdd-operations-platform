// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProductAssistantContext } from "./ProductAssistantContext";
import { ProductSelectionSummary } from "./ProductSelectionSummary";
import { readScope } from "./scope";
import type { ProductsState } from "./data";
import { RegisterScope } from "../../shared/assistant/scopeContext";
import type { AssistantScope } from "../../shared/assistant/contracts";

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
const scope = readScope(new URLSearchParams(), "main", "2026-10-01", "2026-10-31");
const state = {
  version: "v1",
  metadata: { data: { date_from: scope.from, date_to: scope.to } },
  stale: vi.fn(),
} as unknown as ProductsState;
const catalog = {
  shops: [{ value: "s", label: "甲店" }],
  total: 3,
  items: ["小糖豆帽", "小糖豆衣", "其他商品"].map((name, i) => ({
    value: String(i),
    label: `${name} · 甲店 · 100${i}`,
    shop: "s",
    operator: null,
    category: null,
    level: null,
    search_text: `${name} 100${i}`,
  })),
};
function mount(products: string[], keyword: string, ready = true, count = "1") {
  const client = new QueryClient();
  clients.push(client);
  client.setQueryData(["m1", "catalog", "main", "v1", scope.from, scope.to], catalog);
  return render(
    <QueryClientProvider client={client}>
      <ProductSelectionSummary
        scope={{ ...scope, products, keyword }}
        state={state}
        name="main"
        ready={ready}
        count={count}
      />
    </QueryClientProvider>,
  );
}
it("交叉条件显示已选数与实际交集，展开只列参与统计的商品", () => {
  mount(["0", "2"], "小糖豆");
  expect(screen.getByText(/已选 2 个/)).toBeTruthy();
  expect(screen.getByText(/关键词：小糖豆/)).toBeTruthy();
  expect(screen.getByText(/实际匹配 1 个/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看商品" }));
  expect(screen.getByRole("dialog").textContent).toContain("小糖豆帽 · 甲店 · 1000");
  expect(screen.getByRole("dialog").textContent).not.toContain("其他商品");
  expect(screen.getByRole("dialog").textContent).not.toContain("小糖豆衣");
});
it("关键词匹配多个商品且不把候选当作已选", () => {
  mount([], "小糖豆", true, "2");
  expect(screen.queryByText(/已选/)).toBeNull();
  expect(screen.getByText(/匹配 2 个/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看商品" }));
  expect(screen.getByRole("dialog").textContent).toContain("小糖豆衣 · 甲店 · 1001");
});
it("查询未完成不声称匹配零个，空结果明确提示", () => {
  const view = mount(["2"], "小糖豆", false);
  expect(screen.queryByText(/没有匹配商品/)).toBeNull();
  expect((screen.getByRole("button", { name: "查看商品" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  view.unmount();
  mount(["2"], "小糖豆", true, "0");
  expect(screen.getByText("当前条件下没有匹配商品")).toBeTruthy();
});
it("目录名单与经营结果数量不一致时不展示错误名单", () => {
  mount([], "小糖豆", true, "3");
  expect((screen.getByRole("button", { name: "查看商品" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
});

it("助手完整采用四维多选，不把跨店、品类、运营和等级条件扩大为全部", () => {
  const client = new QueryClient();
  clients.push(client);
  client.setQueryData(["m1", "catalog", "main", "v1", scope.from, scope.to], catalog);
  let published: AssistantScope | null = null;
  render(
    <QueryClientProvider client={client}>
      <RegisterScope.Provider
        value={(value) => {
          published = value;
        }}
      >
        <ProductAssistantContext
          scope={{
            ...scope,
            shop: ["s", "t"],
            category: ["衣", "帽"],
            operator: ["甲", "乙"],
            level: ["A", "B"],
            products: ["s/A", "t/B"],
          }}
          version={state.version}
          ready
        />
      </RegisterScope.Provider>
    </QueryClientProvider>,
  );
  expect(published).toMatchObject({
    page: "商品数据",
    subject: null,
    context: {
      domain: "products",
      product_version: "v1",
      listing_ids: ["s/A", "t/B"],
      shop_ids: ["s", "t"],
      category_values: ["衣", "帽"],
      operator_values: ["甲", "乙"],
      level_values: ["A", "B"],
    },
  });
});
