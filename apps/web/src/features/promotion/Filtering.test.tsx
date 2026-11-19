// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePromotion } from "./data";
import { Filters } from "./Filters";
import { Metrics } from "./Overview";
import { Details } from "./Details";
import { Diagnosis } from "./Diagnosis";
import { PromotionPage } from "./PromotionPage";

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
  vi.unstubAllGlobals();
});

const shops = Array.from({ length: 6 }, (_, i) => ({ value: `shop-${i}`, label: `测试店铺${i}` }));
const products = Array.from({ length: 5 }, (_, i) => ({
  value: `listing-${i}`,
  listing_id: `listing-${i}`,
  label: `${404127665593 + i} · 商品${i} · 测试店铺${i}`,
  product_id: String(404127665593 + i),
  product_name: `商品${i}`,
  shop_id: shops[i].value,
  category: `品类${i}`,
  level: `等级${i}`,
  operator: `运营${i}`,
}));
const metric = (value: string | null) => ({
  value,
  label: "测试",
  type: "currency",
  reason: null,
  status: value === null ? "unavailable" : "available",
});

function Harness() {
  const state = usePromotion();
  const location = useLocation();
  return (
    <ConfigProvider theme={{ token: { motion: false } }}>
      <Filters state={state} />
      <button onClick={() => state.setMode(state.mode === "product" ? "store" : "product")}>
        切换模式
      </button>
      <div aria-label="指标值">
        <Metrics
          data={state.overview.data?.data}
          mode={state.mode}
          selected={["spend", "revenue"]}
          toggle={() => {}}
        />
      </div>
      <Details state={state} />
      {state.mode === "product" && <Diagnosis state={state} />}
      <output aria-label="当前网址">{location.search}</output>
      <output aria-label="商品候选数">{state.options.data?.data.total}</output>
    </ConfigProvider>
  );
}

function PageLocation() {
  return <output aria-label="当前网址">{useLocation().search}</output>;
}

function mount(extra = "", wholePage = false) {
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = new URL(input, "http://localhost");
      requests.push(url);
      const q = url.searchParams;
      const selected = products.filter(
        (p) =>
          (!q.get("shop_id") || q.get("shop_id") === p.shop_id) &&
          (!q.get("keyword") || p.product_id.includes(q.get("keyword")!)) &&
          (!q.getAll("listing_ids").length || q.getAll("listing_ids").includes(p.value)) &&
          ["category", "level", "operator"].every(
            (key) => !q.get(key) || p[key as "category"] === q.get(key),
          ),
      );
      const value = selected.length ? String(selected.length * 10) : null;
      const metrics = Object.fromEntries(
        ["spend", "revenue", "roi", "impressions", "clicks", "ctr", "cvr", "cpc"].map((key) => [
          key,
          metric(value),
        ]),
      );
      const data = url.pathname.endsWith("/meta")
        ? {
            mode: q.get("mode"),
            date_from: "2026-10-01",
            date_to: "2026-11-01",
            recommended_from: "2026-10-03",
            recommended_to: "2026-11-01",
            observed_rows: "5",
            complete_through: null,
            completeness: "unknown",
            sources: [],
          }
        : url.pathname.endsWith("/options")
          ? {
              shops,
              products,
              total: 5,
              selected_products: [],
              channels: [],
              categories: products.map((p) => p.category),
              levels: products.map((p) => p.level),
              operators: products.map((p) => p.operator),
            }
          : url.pathname.endsWith("/days")
            ? {
                items: selected.length
                  ? [
                      {
                        date: "2026-11-01",
                        spend: value,
                        revenue: value,
                        net_revenue: value,
                        deals: value,
                        impressions: value,
                        clicks: value,
                        roi: "1",
                        ctr: "1",
                        cvr: "1",
                      },
                    ]
                  : [],
                total: selected.length ? 1 : 0,
                page: 1,
                size: 25,
                total_pages: 1,
                row_grain: "day",
              }
            : url.pathname.endsWith("/diagnosis")
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
                  coverage: {},
                  rule_version: "SOP v1.4",
                  rule_states: {},
                  status: "insufficient",
                }
              : {
                  metrics,
                  net: { net_revenue: metric(value), net_roi: metric(value) },
                  series: [],
                  funnel: [],
                  top5: [],
                  coverage: {},
                  current_record_count: String(selected.length),
                  dataset_record_count: "5",
                  windows: [],
                  channel_summaries: [],
                };
      return {
        ok: true,
        status: 200,
        json: async () => ({ data, meta: { data_version: "v1", request_id: "test" } }),
      };
    }),
  );
  render(
    <MemoryRouter
      initialEntries={[
        `/promotion?mode=product&product_from=2026-10-03&product_to=2026-11-01&store_shop=store-saved${extra}`,
      ]}
    >
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        {wholePage ? (
          <ConfigProvider theme={{ token: { motion: false } }}>
            <PromotionPage />
            <PageLocation />
          </ConfigProvider>
        ) : (
          <Harness />
        )}
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const latest = (path: string) =>
    requests.filter((url) => url.pathname.endsWith(`/${path}`)).at(-1)!.searchParams;
  const spend = () =>
    (wholePage ? screen : within(screen.getByLabelText("指标值"))).getByRole("button", {
      name: /^总花费/,
    }).textContent;
  return { requests, latest, spend };
}

it("旧链接的商品搜索条件可见；清除后指标、明细和诊断同步恢复完整范围", async () => {
  const { latest, spend } = mount("&product_keyword=404127665593");
  await waitFor(() => expect(spend()).toContain("¥10"));
  expect(screen.getByText("商品搜索：404127665593")).toBeTruthy();
  expect(screen.getByLabelText("商品候选数").textContent).toBe("5");
  await userEvent.click(screen.getByRole("button", { name: "清除商品搜索" }));
  await waitFor(() => expect(spend()).toContain("¥50"));
  expect(screen.queryByText("商品搜索：404127665593")).toBeNull();
  expect(screen.getByLabelText("当前网址").textContent).not.toContain("product_keyword");
  await waitFor(() => {
    for (const path of ["overview", "days", "diagnosis"]) {
      expect(latest(path).get("keyword")).toBeNull();
      expect(latest(path).get("date_from")).toBe("2026-10-03");
      expect(latest(path).get("date_to")).toBe("2026-11-01");
      expect(latest(path).get("data_version")).toBe("v1");
    }
  });
  expect(screen.getAllByRole("cell", { name: "¥50" }).length).toBeGreaterThan(0);
});

it("完整推广页：反复切换保留全量选项与已选商品，空组合不放宽，卡片、明细和诊断同范围", async () => {
  // 整页包含全部 Ant Design 控件；并行测试下等待实际状态，不把 1 秒默认限额当产品耗时。
  const settle = (assertion: () => void) => waitFor(assertion, { timeout: 5000 });
  const { requests, latest, spend } = mount("", true);
  await settle(() => expect(spend()).toContain("¥50"));
  expect(screen.getByRole("heading", { name: "拼多多推广数据看板" })).toBeTruthy();
  const choose = async (label: string, text: string) => {
    await userEvent.click(screen.getByLabelText(label));
    await userEvent.click(screen.getByText(text, { selector: ".ant-select-item-option-content" }));
  };
  await choose("产品等级", "等级0");
  await settle(() => expect(spend()).toContain("¥10"));
  for (const [label, choices] of [
    ["店铺筛选", shops.map((s) => s.label)],
    ["品类筛选", products.map((p) => p.category)],
    ["运营筛选", products.map((p) => p.operator)],
  ] as const) {
    await userEvent.click(screen.getByLabelText(label));
    for (const text of choices)
      expect(screen.getByText(text, { selector: ".ant-select-item-option-content" })).toBeTruthy();
    await userEvent.click(screen.getByRole("heading", { name: "拼多多推广数据看板" }));
  }
  await choose("输入商品 ID 或商品名称 筛选", products[0].product_name);
  await settle(() => expect(latest("diagnosis").getAll("listing_ids")).toEqual(["listing-0"]));
  await choose("店铺筛选", "测试店铺5");
  await settle(() => expect(screen.getByText("所选期间暂无推广数据")).toBeTruthy());
  expect(spend()).toContain("—");
  expect(screen.getByLabelText("当前网址").textContent).toContain("product_products=listing-0");
  await settle(() => {
    for (const path of ["overview", "days", "diagnosis"]) {
      expect(latest(path).get("shop_id")).toBe("shop-5");
      expect(latest(path).get("level")).toBe("等级0");
      expect(latest(path).getAll("listing_ids")).toEqual(["listing-0"]);
    }
  });
  const readsBeforeClear = requests.filter((u) => u.pathname.endsWith("/overview")).length;
  await userEvent.click(screen.getByRole("button", { name: "清空筛选" }));
  await settle(() => expect(spend()).toContain("¥50"));
  expect(screen.getAllByRole("cell", { name: "¥50" }).length).toBeGreaterThan(0);
  expect(requests.filter((u) => u.pathname.endsWith("/options"))).toHaveLength(1);
  expect(requests.filter((u) => u.pathname.endsWith("/overview"))).toHaveLength(readsBeforeClear);
}, 15000);

it("六店始终可选，无记录店显示空结果，清空筛选恢复数据并保留日期和另一模式条件", async () => {
  const { latest, spend } = mount(
    "&product_keyword=404127665593&product_category=品类0&product_level=等级0&product_operator=运营0",
  );
  await waitFor(() => expect(spend()).toContain("¥10"));
  await userEvent.click(screen.getByLabelText("店铺筛选"));
  for (const shop of shops)
    expect(
      screen.getByText(shop.label, { selector: ".ant-select-item-option-content" }),
    ).toBeTruthy();
  await userEvent.click(
    screen.getByText("测试店铺5", { selector: ".ant-select-item-option-content" }),
  );
  await waitFor(() => expect(latest("overview").get("shop_id")).toBe("shop-5"));
  await waitFor(() => expect(spend()).toContain("—"));
  expect(screen.getByLabelText("商品候选数").textContent).toBe("5");
  expect(latest("overview").get("keyword")).toBe("404127665593");
  expect(latest("overview").get("category")).toBe("品类0");
  expect(latest("overview").get("level")).toBe("等级0");
  expect(latest("overview").get("operator")).toBe("运营0");
  await userEvent.click(screen.getByRole("button", { name: "清空筛选" }));
  await waitFor(() => expect(spend()).toContain("¥50"));
  expect(screen.getByLabelText("商品候选数").textContent).toBe("5");
  const url = new URLSearchParams(screen.getByLabelText("当前网址").textContent!);
  expect(url.get("product_from")).toBe("2026-10-03");
  expect(url.get("product_to")).toBe("2026-11-01");
  expect(url.get("store_shop")).toBe("store-saved");
  for (const key of ["shop", "keyword", "category", "level", "operator", "products"])
    expect(url.has(`product_${key}`)).toBe(false);
});

it("商品搜索草稿收起即清空，已选商品保留且全部结果使用同一商品身份", async () => {
  const { latest, spend, requests } = mount();
  await waitFor(() => expect(spend()).toContain("¥50"));
  const search = screen.getByLabelText("输入商品 ID 或商品名称 筛选");
  await userEvent.type(search, "404127665593");
  expect(screen.getByLabelText("商品候选数").textContent).toBe("1");
  await userEvent.click(screen.getByLabelText("指标值"));
  await waitFor(() => expect(screen.getByLabelText("商品候选数").textContent).toBe("5"));
  expect((search as HTMLInputElement).value).toBe("");
  await userEvent.click(search);
  await userEvent.click(
    screen.getByText(products[0].product_name, { selector: ".ant-select-item-option-content" }),
  );
  await waitFor(() => expect(spend()).toContain("¥10"));
  for (const path of ["overview", "days", "diagnosis"])
    expect(latest(path).getAll("listing_ids")).toEqual(["listing-0"]);
  expect(
    screen.getByText(products[0].product_name, {
      selector: ".ant-select-item-option-selected .ant-select-item-option-content",
    }),
  ).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "清空筛选" }));
  await waitFor(() => expect(spend()).toContain("¥50"));
  expect(requests.filter((url) => url.pathname.endsWith("/options"))).toHaveLength(1);
});
