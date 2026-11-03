// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { PromotionDetails } from "./PromotionDetails";
import { testMetrics, testFilters } from "../testFixtures";

const source = {
  state: "observed",
  observed_through: "2026-11-01",
  source_updated_text: null,
  last_success_at: "2026-11-02T03:02:13Z",
  last_attempt_at: "2026-11-02T03:02:08Z",
  last_attempt_status: "succeeded",
};
const product = {
  code: "shop_channel_product_promotion",
  latest_observed_date: "2026-10-26",
  spend: "10.20",
  revenue: "99.99",
  ctr: "0.1",
  roi: "9.802941176471",
  missing_shop_days: 0,
  null_counts: { spend: 0, attributed_revenue: 0, clicks: 0, impressions: 0 },
  record_issues: [],
  source,
};
const history = [
  {
    shop_id: "spigf",
    shop_name: "官方店原名",
    date: "2026-10-26",
    source: "飞书历史补充",
    source_url: "https://example.com/history",
    source_record_id: "one",
    spend: "1.23",
    revenue: "6.78",
    status: "supplement" as const,
  },
  {
    shop_id: "spigf",
    shop_name: "官方店原名",
    date: "2026-10-25",
    source: "飞书历史补充",
    source_url: null,
    source_record_id: "two",
    spend: "20",
    revenue: "40",
    status: "warehouse" as const,
  },
  {
    shop_id: "spihw",
    shop_name: "户外店原名",
    date: "2026-02-21",
    source: "原版停投确认记录",
    source_url: null,
    source_record_id: null,
    spend: "0",
    revenue: "0",
    status: "outside_range" as const,
  },
];
const data = {
  ...testMetrics,
  promotion_spend: "10.20",
  promotion_ctr: "0.1",
  promotion_roi: "9.8",
  promotion_channels: [product],
  promotion_supplements: history,
};
afterEach(cleanup);

it("完整数据、真实零及已生效历史补充均不展示整块", () => {
  const { container, rerender } = render(<PromotionDetails data={data} />);
  expect(container.innerHTML).toBe("");
  rerender(
    <PromotionDetails
      data={{
        ...data,
        promotion_spend: "0",
        promotion_ctr: null,
        promotion_roi: null,
        promotion_channels: [{ ...product, spend: "0", revenue: "0", ctr: null, roi: null }],
      }}
    />,
  );
  expect(container.innerHTML).toBe("");
  rerender(<PromotionDetails />);
  expect(container.innerHTML).toBe("");
});

it("缺行和空字段按实际指标结果提示，展开后才列店铺、渠道及日期", async () => {
  const { container } = render(
    <PromotionDetails
      shops={testFilters.data.shops}
      shopName="全部店铺"
      data={{
        ...data,
        promotion_roi: null,
        field_states: { promotion_roi: "incomplete_channel_coverage" },
        promotion_channels: [
          {
            ...product,
            missing_shop_days: 1,
            null_counts: { ...product.null_counts, attributed_revenue: 1 },
            record_issues: [
              {
                shop_id: "spigf",
                date: "2026-10-26",
                kind: "null_fields",
                fields: ["attributed_revenue"],
              },
              { shop_id: "spigf", date: "2026-10-27", kind: "missing_record", fields: [] },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/推广花费按已有金额汇总/).textContent).toContain("ROI暂无数据");
  expect(container.querySelector("details")!.open).toBe(false);
  await userEvent.click(screen.getByText("查看异常明细"));
  const detail = container.querySelector("details")!;
  expect(detail.textContent).toContain("官方店 · 商品推广 · 推广成交额为空（有记录）：2026-10-26");
  expect(detail.textContent).toContain("官方店 · 商品推广 · 无推广记录：2026-10-27");
  expect(container.querySelector("table")).toBeNull();
  expect(container.textContent).not.toMatch(/历史补充|全部历史|数仓来源|ROI＝|点击率＝/);
});

it("只有点击字段为空时仅提示点击率，不将有值ROI或真实零花费列为异常", () => {
  const { container } = render(
    <PromotionDetails
      data={{
        ...data,
        promotion_spend: "0",
        promotion_ctr: null,
        promotion_channels: [
          {
            ...product,
            null_counts: { ...product.null_counts, clicks: 1 },
            record_issues: [
              { shop_id: "spigf", date: "2026-10-26", kind: "null_fields", fields: ["clicks"] },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/本期推广数据不完整/).textContent).toContain("点击率暂无数据");
  expect(container.textContent).not.toMatch(/ROI|推广花费按/);
});

it("单渠道部分缺记录仍有结果时不误报无法计算，换店和日期不残留旧异常", async () => {
  const missing = {
    ...product,
    missing_shop_days: 1,
    record_issues: [
      { shop_id: "spigf", date: "2026-10-27", kind: "missing_record" as const, fields: [] },
    ],
  };
  const { container, rerender } = render(
    <PromotionDetails
      data={{ ...data, promotion_channels: [missing] }}
      shops={testFilters.data.shops}
    />,
  );
  expect(screen.getByText(/本期推广数据不完整/).textContent).toContain("ROI按已有完整记录计算");
  await userEvent.click(screen.getByText("查看异常明细"));
  rerender(
    <PromotionDetails
      shops={testFilters.data.shops}
      shopName="户外店"
      data={{
        ...data,
        coverage: { ...data.coverage, window_start: "2026-11-01", window_end: "2026-11-01" },
        promotion_channels: [
          {
            ...missing,
            record_issues: [
              { shop_id: "spihw", date: "2026-11-01", kind: "missing_record", fields: [] },
            ],
          },
        ],
      }}
    />,
  );
  expect(container.textContent).toContain("户外店 · 商品推广 · 无推广记录：2026-11-01");
  expect(container.textContent).not.toMatch(/官方店|2026-10-27|明星店铺/);
  rerender(<PromotionDetails data={data} />);
  expect(container.innerHTML).toBe("");
});

it("没有接入数据不推断漏采，缺失日期未返回时不编造明细", async () => {
  const { container, rerender } = render(
    <PromotionDetails
      data={{
        ...data,
        promotion_spend: null,
        promotion_ctr: null,
        promotion_roi: null,
        promotion_channels: [
          {
            ...product,
            latest_observed_date: null,
            source: { ...source, state: "not_connected" },
            missing_shop_days: 1,
            record_issues: [],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(/本期推广数据不完整/).textContent).toContain("推广花费暂无数据");
  await userEvent.click(screen.getByText("查看异常明细"));
  expect(container.textContent).toContain("商品推广：暂无可用数据");
  expect(container.textContent).not.toContain("无推广记录");
  rerender(
    <PromotionDetails
      data={{
        ...data,
        promotion_channels: [{ ...product, missing_shop_days: 1, record_issues: undefined }],
      }}
    />,
  );
  expect(container.textContent).toContain("详细日期暂未取得");
});

it("适用渠道检查失败显示实际保旧状态，不展示其他店的失败渠道", () => {
  const sources = {
    [product.code]: { ...source, observed_from: "2026-02-01", last_attempt_status: "failed" },
  };
  const { container, rerender } = render(<PromotionDetails data={data} sources={sources} />);
  expect(
    screen.getByText(/商品推广：最近检查未成功，当前显示上次成功数据/).closest("details"),
  ).toBeNull();
  expect(container.textContent).not.toContain("本期推广数据不完整");
  rerender(
    <PromotionDetails data={data} sources={{ shop_channel_star_shop: sources[product.code] }} />,
  );
  expect(container.innerHTML).toBe("");
});
