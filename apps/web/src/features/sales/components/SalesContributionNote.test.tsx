// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { FiltersResponse, ShopItem } from "../api/contracts";
import { testShops } from "../testFixtures";
import { REFUND_SOURCE } from "../sourceLinks";
import { SalesContributionNote } from "./SalesContributionNote";

afterEach(cleanup);
const source = {
  state: "observed",
  observed_from: "2026-02-21",
  observed_through: "2026-11-01",
  source_updated_text: null,
};
const sources: FiltersResponse["data"]["sources"] = {
  shop_refunds: source,
  sales_targets: source,
};
const product = {
  code: "shop_channel_product_promotion",
  latest_observed_date: "2026-02-21",
  spend: "0",
  revenue: "0",
  ctr: null,
  roi: null,
  missing_shop_days: 0,
  null_counts: {},
  source,
};
const history = {
  shop_id: "spihw",
  shop_name: "户外店",
  date: "2026-02-21",
  source: "飞书历史补齐",
  source_url: null,
  source_record_id: "test",
  spend: "0",
  revenue: "0",
  status: "supplement" as const,
};
const shops: ShopItem[] = testShops.map((shop, i) => ({
  ...shop,
  coverage: {
    ...shop.coverage,
    window_start: "2026-02-21",
    window_end: "2026-02-21",
    observed_days: 1,
  },
  promotion_channels:
    i === 0 ? [product, { ...product, code: "shop_channel_star_shop" }] : [product],
  promotion_supplements: i === 0 ? [] : [history],
}));

it("恢复线上三行结构；真实零计入有记录店铺，历史补充不冒充数仓记录", () => {
  const { container, rerender } = render(
    <SalesContributionNote shops={shops} sources={sources} error={null} />,
  );
  expect(container.querySelectorAll("dl > div")).toHaveLength(3);
  expect(screen.getByText("2026-02-21").getAttribute("datetime")).toBe("2026-02-21");
  expect(screen.getByText("2 家店铺")).toBeTruthy();
  expect(screen.getByText("数仓 1 店 + 官方明星店铺 + 历史补充 1 条")).toBeTruthy();
  expect(screen.getByText("飞书多维表")).toBeTruthy();
  expect(screen.getByRole("link", { name: /查看统计退款金额/ }).getAttribute("href")).toBe(
    REFUND_SOURCE,
  );
  // 同店同日被数仓替换后，只改来源计数，不继续列为补充。
  rerender(
    <SalesContributionNote
      shops={[
        shops[0],
        { ...shops[1], promotion_supplements: [{ ...history, status: "warehouse" }] },
      ]}
      sources={sources}
      error={null}
    />,
  );
  expect(screen.getByText("数仓 2 店 + 官方明星店铺")).toBeTruthy();
  expect(container.textContent).not.toContain("历史补充 1 条");
});

it("日期范围与来源状态更新后，空期间、未接入和读取中分别表达", () => {
  const empty: ShopItem[] = shops.map((shop) => ({
    ...shop,
    shop_revenue: null,
    coverage: {
      ...shop.coverage,
      window_start: "2026-03-01",
      window_end: "2026-03-01",
      observed_days: 0,
    },
    promotion_channels: shop.promotion_channels?.map((c) => ({
      ...c,
      latest_observed_date: null,
      missing_shop_days: 1,
    })),
    promotion_supplements: [history],
  }));
  const { container, rerender } = render(
    <SalesContributionNote
      shops={empty}
      sources={{ ...sources, sales_targets: { ...source, state: "not_connected" } }}
      error={null}
    />,
  );
  expect(screen.getByText("本期暂无销售记录")).toBeTruthy();
  expect(screen.getByText("2026-03-01").getAttribute("datetime")).toBe("2026-03-01");
  expect(screen.getByText("本期暂无推广记录")).toBeTruthy();
  expect(container.textContent).toContain("退款：飞书多维表；目标：暂无可用数据");
  expect(container.textContent).not.toMatch(/历史补充 1 条|6 家店铺|数仓 6 店/);
  rerender(<SalesContributionNote shops={[]} error={null} />);
  expect(container.textContent).toContain("正在读取");
  expect(container.querySelector("time")).toBeNull();
  expect(container.textContent).not.toContain("本期暂无销售记录");
  rerender(<SalesContributionNote shops={[]} error="读取失败" />);
  expect(container.textContent).toContain("读取失败");
});
