// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Board } from "./contracts";
import { ProductDiagnosis } from "./ProductDiagnosis";

afterEach(cleanup);
const data = {
  scope: { start: "2026-10-02", end: "2026-11-01" },
  metrics: {
    revenue: {
      label: "交易金额",
      type: "currency",
      value: "4399115.63",
      previous: "2077585.91",
      yoy: "1.117416954",
    },
    visitors: {
      label: "商品访客数",
      type: "number",
      value: "2303269",
      previous: "1106813",
      yoy: "1.080992",
    },
    conversion: {
      label: "支付转化率",
      type: "percent",
      value: "0.0406739",
      previous: "0.0425501",
      yoy: "-0.044093",
    },
  },
  health: { title: "交易额同比下降", detail: "同款下降42.23%" },
  diagnosis: {
    generation_method: "rules",
    growth_title: "交易增长",
    traffic_title: "流量变化",
    action: "检查主图、价格与详情页承接。",
  },
} as unknown as Board;

it("与七卡同一同比和日期，不借用健康度结论，也不冒充AI", () => {
  render(<ProductDiagnosis data={data} metric="revenue" />);
  expect(screen.getByText("交易金额同比+111.7%，当前为¥4,399,116。")).toBeTruthy();
  expect(screen.getByText("商品访客数同比+108.1%，支付转化率同比-4.4%。")).toBeTruthy();
  expect(screen.getByText("2026-10-02 至 2026-11-01")).toBeTruthy();
  expect(screen.getByText("规则诊断")).toBeTruthy();
  expect(screen.queryByText(/42.23|AI经营|完整日|来源覆盖/)).toBeNull();
});

it("切换关注指标只换底部数值，经营诊断仍按交易、流量和转化", () => {
  const { rerender } = render(<ProductDiagnosis data={data} metric="revenue" />);
  rerender(<ProductDiagnosis data={data} metric="conversion" />);
  expect(screen.getByText("4.07%")).toBeTruthy();
  expect(screen.getByText("当前支付转化率")).toBeTruthy();
  expect(screen.getByText("交易增长")).toBeTruthy();
});

it("无同比不显示零涨幅，无数据不补零", () => {
  const missing = structuredClone(data);
  Object.assign(missing.metrics.revenue, { value: null, previous: null, yoy: null });
  Object.assign(missing.metrics.visitors, { previous: "0", yoy: null });
  render(<ProductDiagnosis data={missing} metric="revenue" />);
  expect(screen.getByText("交易金额暂无同比结果，当前为—。")).toBeTruthy();
  expect(screen.getByText(/商品访客数去年同期为0/)).toBeTruthy();
  expect(screen.queryByText(/同比0.0%/)).toBeNull();
});
