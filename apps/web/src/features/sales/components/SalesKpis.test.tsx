// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SalesKpis } from "./SalesKpis";
import { testMetrics, testShops } from "../testFixtures";

const source = {
  state: "observed",
  observed_from: "2026-10-01",
  observed_through: "2026-10-27",
  source_updated_text: null,
  last_attempt_status: "succeeded",
  last_attempt_at: "2026-10-28T01:00:00Z",
  last_success_at: "2026-10-28T01:00:01Z",
};
const match = {
  matched_refunds: "10.25",
  matched_sales: "100.50",
  matched_days: 1,
  sales_coverage: "1",
  excluded_days: 2,
  excluded_sales: "0.00",
  missing_dates: ["2026-10-26", "2026-10-27"],
  state: "same_day_reference",
  source,
};
const channel = {
  code: "shop_channel_product_promotion",
  spend: "10",
  revenue: "50",
  ctr: "0.1",
  roi: "5",
  missing_shop_days: 0,
  null_counts: {},
  source,
};
const data = {
  ...testMetrics,
  refund_rate: "0.101990049751",
  refund_match: match,
  promotion_channels: [channel, { ...channel, code: "shop_channel_star_shop" }],
};
const shops = testShops.map((shop, i) => ({
  ...shop,
  refund_match: { ...match, excluded_days: 1, missing_dates: [i ? "2026-10-27" : "2026-10-26"] },
  refund_coverage: {
    state: "observed" as const,
    null_amount_dates: i ? [] : ["2026-10-26"],
    missing_record_dates: i ? ["2026-10-27"] : [],
  },
}));
const props = {
  data,
  shops,
  shopId: "",
  shopName: "全部店铺",
  rangeLabel: "2026-10-26 至 2026-10-27",
};
beforeEach(() => {
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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("八卡使用业务文案和具体日期，计算明细初始隐藏且没有原长说明", () => {
  const { container } = render(<SalesKpis {...props} />);
  expect(container.querySelectorAll(".kpi-card")).toHaveLength(8);
  expect(screen.getByText("退款金额占比")).toBeTruthy();
  expect(screen.getByText("2026-10-26 至 2026-10-27 · 每日访客累计")).toBeTruthy();
  expect(screen.getByText("商品推广＋明星店铺")).toBeTruthy();
  expect(screen.getByText("成交买家数 ÷ 访客数")).toBeTruthy();
  expect(screen.getByText("销售金额 ÷ 成交买家数")).toBeTruthy();
  expect(screen.getByText("推广点击量 ÷ 曝光量")).toBeTruthy();
  expect(screen.getByText("推广成交金额 ÷ 推广花费")).toBeTruthy();
  expect(screen.queryByLabelText("退款金额占比计算明细")).toBeNull();
  expect(container.textContent).not.toMatch(/数仓优先|匹配店铺日|销售覆盖率|所选日期/);
});

it("点击后显示API金额两位小数与原比率，分别解释空金额及缺记录", async () => {
  render(<SalesKpis {...props} />);
  await userEvent.click(screen.getByRole("button", { name: "计算明细" }));
  const content = await screen.findByLabelText("退款金额占比计算明细");
  expect(content.textContent).toContain("退款金额 ¥10.25 ÷ 销售金额 ¥100.50 = 10.20%");
  const rows = within(content).getAllByRole("listitem");
  expect(rows[0].textContent).toContain("官方店 · 2026-10-26：退款金额为空（有记录）");
  expect(rows[1].textContent).toContain("户外店 · 2026-10-27：无退款记录");
  expect(content.textContent).toContain("两项金额都有数据才参与计算");
  expect(content.textContent).not.toMatch(/暂无退款|没有退款|数仓|入仓|覆盖率/);
});

it("日期跨年和选店后弹层更新，不残留其他店排除项或明星店铺", async () => {
  const { rerender } = render(<SalesKpis {...props} />);
  await userEvent.click(screen.getByRole("button", { name: "计算明细" }));
  await screen.findByLabelText("退款金额占比计算明细");
  rerender(
    <SalesKpis
      {...props}
      shopId="spihw"
      shopName="户外店"
      rangeLabel="2026-12-31 至 2027-01-01"
      data={{
        ...data,
        promotion_channels: [channel],
        refund_match: { ...match, excluded_days: 1, missing_dates: ["2027-01-01"] },
      }}
      shops={[
        shops[0],
        {
          ...shops[1],
          refund_match: { ...match, missing_dates: ["2027-01-01"] },
          refund_coverage: {
            state: "observed",
            null_amount_dates: [],
            missing_record_dates: ["2027-01-01"],
          },
        },
      ]}
    />,
  );
  const content = screen.getByLabelText("退款金额占比计算明细");
  expect(screen.getByText("2026-12-31 至 2027-01-01 · 每日访客累计")).toBeTruthy();
  expect(content.textContent).toContain("户外店 · 2027-01-01：无退款记录");
  expect(content.textContent).not.toMatch(/官方店|2026-10-/);
  expect(screen.getByTestId("sales-kpis").textContent).not.toContain("明星店铺");
  rerender(<SalesKpis {...props} data={undefined} />);
  expect(screen.getByLabelText("退款金额占比计算明细").textContent).not.toMatch(/10.25|未参与计算/);
  expect((screen.getByRole("button", { name: "计算明细" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
});

it("真实零退款保留0%，零销售或无数据不伪造可计算结果", async () => {
  const zero = {
    ...data,
    refund_rate: "0",
    refund_match: { ...match, matched_refunds: "0", excluded_days: 0, missing_dates: [] },
  };
  const { rerender } = render(<SalesKpis {...props} data={zero} shops={[]} />);
  await userEvent.click(screen.getByRole("button", { name: "计算明细" }));
  expect((await screen.findByLabelText("退款金额占比计算明细")).textContent).toContain(
    "¥0.00 ÷ 销售金额 ¥100.50 = 0.00%",
  );
  rerender(
    <SalesKpis
      {...props}
      data={{
        ...zero,
        refund_rate: null,
        refund_match: { ...zero.refund_match, matched_sales: "0" },
      }}
      shops={[]}
    />,
  );
  expect(screen.getByLabelText("退款金额占比计算明细").textContent).toContain("销售金额未大于0元");
  expect(screen.getByLabelText("退款金额占比计算明细").textContent).not.toContain("= 0.00%");
  rerender(
    <SalesKpis
      {...props}
      data={{
        ...testMetrics,
        refund_match: {
          ...match,
          matched_refunds: null,
          matched_sales: null,
          matched_days: 0,
          excluded_days: 0,
        },
      }}
      shops={[]}
    />,
  );
  expect(screen.getByLabelText("退款金额占比计算明细").textContent).toContain("本期暂无可用于计算");
});

it("只列实际排除日期，旧响应无法区分原因时不猜测空值或缺行", async () => {
  render(<SalesKpis {...props} shops={[{ ...shops[0], refund_coverage: undefined }]} />);
  await userEvent.click(screen.getByRole("button", { name: "计算明细" }));
  const content = await screen.findByLabelText("退款金额占比计算明细");
  expect(content.textContent).toContain("官方店 · 2026-10-26：退款金额未取得");
  expect(content.textContent).not.toMatch(/为空|无退款记录/);
});

it("退款更新失败在卡片保留短提示，检查时间只放计算明细", async () => {
  render(
    <SalesKpis
      {...props}
      sources={{ shop_refunds: { ...source, last_attempt_status: "failed" } }}
    />,
  );
  expect(screen.getByTestId("sales-kpis").textContent).toContain(
    "退款数据更新未成功，当前显示上次成功数据。",
  );
  expect(screen.getByTestId("sales-kpis").textContent).not.toContain("最近检查时间");
  await userEvent.click(screen.getByRole("button", { name: "计算明细" }));
  expect((await screen.findByLabelText("退款金额占比计算明细")).textContent).toContain(
    "最近检查时间",
  );
});
