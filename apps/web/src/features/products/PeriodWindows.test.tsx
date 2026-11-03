// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Board } from "./contracts";
import { PeriodWindows } from "./PeriodWindows";

afterEach(cleanup);

const data = {
  metrics: {
    revenue: { label: "交易金额", type: "currency" },
    conversion: { label: "支付转化率", type: "percent" },
  },
  windows: [
    {
      days: 7,
      start: "2026-10-26",
      end: "2026-11-01",
      previous_start: "2026-10-19",
      previous_end: "2026-10-25",
      metrics: {
        revenue: {
          value: "200",
          previous: "100",
          change: "1",
          reason: null,
          message: null,
          status: "observed",
          comparison_status: "observed_reference",
        },
        conversion: {
          value: "0.05",
          previous: "0.10",
          change: "-0.5",
          reason: null,
          message: null,
          status: "observed",
          comparison_status: "observed_reference",
        },
      },
    },
  ],
} as unknown as Board;

describe("固定周期趋势", () => {
  it("随当前指标切换，列出实际两期日期和前期值", () => {
    const { rerender } = render(<PeriodWindows data={data} metric="revenue" />);
    expect(screen.getByText("交易金额周期表现")).toBeTruthy();
    expect(screen.getByText("较前7日 +100.0%")).toBeTruthy();
    const details = screen.getByRole("table", { hidden: true });
    expect(within(details).getByText("2026-10-26 至 2026-11-01")).toBeTruthy();
    expect(within(details).getByText("2026-10-19 至 2026-10-25")).toBeTruthy();
    expect(within(details).getByText("¥100")).toBeTruthy();
    rerender(<PeriodWindows data={data} metric="conversion" />);
    expect(screen.getByText("支付转化率周期表现")).toBeTruthy();
    expect(screen.getByText("较前7日 -50.0%")).toBeTruthy();
    expect(screen.getByText("5.00%")).toBeTruthy();
  });
  it("展示空值的具体原因，真实0仍显示为0，不冒充零涨跌", () => {
    const empty = structuredClone(data);
    Object.assign(empty.windows[0].metrics.revenue, {
      value: "0",
      previous: null,
      change: null,
      reason: "no_records",
      message: "前7日暂无经营记录",
    });
    render(<PeriodWindows data={empty} metric="revenue" />);
    expect(screen.getByText("¥0")).toBeTruthy();
    expect(screen.getByText("前7日暂无经营记录")).toBeTruthy();
    expect(screen.queryByText(/无可比基数|0.0%|覆盖不全/)).not.toBeTruthy();
  });
});
