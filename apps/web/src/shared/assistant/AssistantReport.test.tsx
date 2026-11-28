// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantReportView, ProductReportChart } from "./AssistantReport";
import type { AssistantReport } from "./contracts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("图表可切换指标，明细保留缺值、真实零和百分比单位", () => {
  const report: AssistantReport = {
    kind: "product_performance",
    product: { listing_id: "s/a", shop_id: "s", name: "测试款", shop_name: "店" },
    period: {
      date_from: "2026-11-02",
      date_to: "2026-11-04",
      previous_from: "2026-10-31",
      previous_to: "2026-11-01",
    },
    metrics: [
      {
        key: "revenue",
        label: "交易金额",
        unit: "currency",
        current: "0",
        previous: "1.25",
        status: "partial",
        reason: "部分日期缺少成交字段",
      },
      {
        key: "conversion",
        label: "支付转化率",
        unit: "percent",
        current: "0.125",
        previous: null,
        status: "observed",
        reason: null,
      },
    ],
    data_version: "test",
    evidence: {
      source: "商品日经营",
      rules: {
        version: "risk-test",
        status: "evaluated",
        date_to: "2026-11-03",
        baseline_from: "2026-10-27",
        baseline_to: "2026-11-02",
        ranking_basis: "同店所选期成交额前20%",
        findings: [{ label: "成交下滑", action: "核对价格变更", basis: "测试规则依据" }],
      },
    },
    coverage: {
      current: { observed_days: 2, expected_days: 3, missing_dates: ["2026-11-03"] },
      previous: { observed_days: 2, expected_days: 2, missing_dates: [] },
      complete_through: null,
    },
    series: [
      { date: "2026-11-02", values: { revenue: "0", conversion: "0.125" } },
      { date: "2026-11-03", values: { revenue: null, conversion: null } },
      { date: "2026-11-04", values: { revenue: "20", conversion: "0.1" } },
    ],
  };
  class Observer {
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", Observer);
  render(
    <>
      <ProductReportChart report={report} question="看成交趋势" />
      <AssistantReportView report={report} question="看成交趋势" />
    </>,
  );
  expect(screen.getAllByText("¥0.00")[0]).toBeTruthy();
  expect(screen.getByText("部分日期缺少成交字段")).toBeTruthy();
  expect(screen.getByText("成交下滑")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看明细" }));
  expect(screen.getByText("同店所选期成交额前20%")).toBeTruthy();
  expect(screen.getByText(/2026-10-27 至 2026-11-02/)).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "趋势指标" }), {
    target: { value: "conversion" },
  });
  expect(screen.getByRole("img", { name: /支付转化率.*趋势/ })).toBeTruthy();
  expect(screen.getByText("2026-11-02").closest("tr")?.textContent).toContain("12.50%");
  expect(screen.getByText("2026-11-03").closest("tr")?.textContent).toContain("—");
});
