// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { SalesRegions } from "./components/SalesRegions";
import { SalesRatings } from "./components/SalesRatings";
import { PromotionDetails } from "./components/PromotionDetails";
import { SourceState } from "./components/SourceState";
import { ShopTable } from "./components/ShopTable";
import { testFilters, testMeta, testMetrics, testShops } from "./testFixtures";

const source = {
  state: "observed",
  observed_from: "2026-10-26",
  observed_through: "2026-10-26",
  source_updated_text: null,
  last_success_at: "2026-10-27T00:00:00Z",
  last_attempt_status: "failed",
  last_attempt_at: "2026-10-28T00:00:00Z",
};
const filters = {
  ...testFilters,
  data: {
    ...testFilters.data,
    capabilities: { regions: true, product_regions: true, ratings: true },
    sources: { shop_regions: source, product_regions: source, shop_ratings: source },
  },
};
const onConflict = () => {};
beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const style = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((e) => style(e));
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      const product = path.includes("product-regions");
      const item = {
        region: "浙江",
        transaction_amount: "100",
        transaction_orders: "2",
        transaction_buyers: "1",
        aov: "100",
        actual_shipped_amount: "100",
        actual_shipped_qty: "2",
        actual_returned_amount: null,
        actual_returned_qty: "0",
        refund_rate: null,
        share: "0.5",
        null_counts: {
          actual_shipped_amount: 1,
          actual_shipped_qty: 1,
          actual_returned_amount: 2,
          actual_returned_qty: 0,
        },
      };
      let data: Record<string, unknown> = {
        items: [item],
        totals: {
          transaction_amount: "100",
          transaction_orders: "2",
          transaction_buyers: "1",
          actual_shipped_amount: "100",
          actual_shipped_qty: "2",
          actual_returned_amount: null,
          actual_returned_qty: "0",
        },
        region_count: 1,
        null_counts: product ? item.null_counts : {},
        missing_shop_days: [],
        source,
      };
      if (path.includes("/filters"))
        data = { categories: [], products: [], total: 0, offset: 0, limit: 100, source };
      if (path.includes("/ratings"))
        data = { latest_date: "2026-10-26", latest: [], points: [], source };
      return Promise.resolve({ ok: true, json: async () => ({ data, meta: testMeta }) });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("产品逐格部分缺值及份额分母缺项明确标星，全空保留空值", async () => {
  render(<SalesRegions filters={filters} onConflict={onConflict} />);
  await waitFor(() =>
    expect(
      within(screen.getByRole("table", { name: "聚水潭产品地区数据" })).getByText("浙江"),
    ).toBeTruthy(),
  );
  const cells = within(screen.getByRole("table", { name: "聚水潭产品地区数据" }))
    .getByRole("row", { name: /浙江/ })
    .querySelectorAll("td");
  expect(cells[1].textContent).toContain("*");
  expect(cells[2].textContent).toContain("*");
  expect(cells[3].textContent).toContain("*");
  expect(cells[4].textContent).toBe("—");
  expect(cells[5].textContent).toBe("0");
  expect(cells[6].textContent).toBe("—");
  expect(within(cells[2]).getByTitle(/分母缺失/)).toBeTruthy();
});
it("后台地区金额恢复原型行内占比条", async () => {
  render(<SalesRegions filters={filters} onConflict={onConflict} />);
  await waitFor(() =>
    expect(
      within(screen.getByRole("table", { name: "店铺后台地区数据" })).getByText("浙江"),
    ).toBeTruthy(),
  );
  const bar = screen
    .getByRole("table", { name: "店铺后台地区数据" })
    .querySelector(".region-sales em") as HTMLElement;
  expect(bar).not.toBeNull();
  expect(bar.style.width).toBe("50%");
});
it("各来源失败明确保留上次成功值及日期，退款也独立显示", async () => {
  const { container } = render(
    <>
      <SourceState source={source} label="飞书退款" />
      <SalesRegions filters={filters} onConflict={onConflict} />
      <SalesRatings filters={filters} shop="" onConflict={onConflict} />
      <PromotionDetails
        data={{
          ...testMetrics,
          promotion_channels: [
            {
              code: "shop_channel_product_promotion",
              spend: "1",
              revenue: "2",
              ctr: "0.1",
              roi: "2",
              missing_shop_days: 1,
              null_counts: {},
              source,
            },
          ],
        }}
      />
    </>,
  );
  await waitFor(() =>
    expect(
      container.textContent?.match(/保留上次成功值|当前显示上次成功数据/g)?.length,
    ).toBeGreaterThanOrEqual(5),
  );
  expect(
    screen.getByText(/商品推广：最近检查未成功，当前显示上次成功数据/).closest("details"),
  ).toBeNull();
  expect(container.textContent).toContain("最近成功");
  expect(container.textContent).toContain("2026");
});
it("底表配对子集退款率标已匹配并说明缺日覆盖", () => {
  render(
    <ShopTable
      shops={[
        {
          ...testShops[0],
          refund_rate: "0.2",
          refund_match: {
            matched_refunds: "20",
            matched_sales: "100",
            matched_days: 1,
            sales_coverage: "0.5",
            excluded_days: 1,
            excluded_sales: "100",
            missing_dates: ["2026-10-25"],
            state: "same_day_reference",
          },
        },
      ]}
      selected=""
      onSelect={() => {}}
    />,
  );
  expect(screen.getByText(/已匹配/)).toBeTruthy();
  expect(screen.getByTitle(/排除1个店铺日/)).toBeTruthy();
});
