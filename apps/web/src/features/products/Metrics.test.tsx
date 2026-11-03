// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Metrics } from "./Overview";
import type { Board } from "./contracts";

afterEach(cleanup);

it("shows period totals with compact rates, real dates and unchanged metric selection", () => {
  const pair = {
    value: "1000",
    previous: "50",
    change: "19",
    reason: null,
    message: null,
    current_rows: 2,
    previous_rows: 1,
  };
  const data = {
    comparison_periods: {
      current: { start: "2026-10-16", end: "2026-10-31" },
      yoy: { start: "2025-10-16", end: "2025-10-31" },
      mom: { start: "2026-09-30", end: "2026-10-15" },
    },
    metrics: {
      revenue: {
        label: "交易金额",
        value: "1000",
        previous: "50",
        previous_period: null,
        yoy: "19",
        mom: null,
        type: "currency",
        status: "observed",
        reason: null,
        yoy_comparison: pair,
        mom_comparison: {
          ...pair,
          value: null,
          previous: null,
          change: null,
          reason: "no_records",
          message: "前一期暂无经营记录",
          previous_rows: 0,
        },
      },
    },
  } as unknown as Board;
  const toggle = vi.fn();
  const { rerender } = render(<Metrics data={data} active={["revenue"]} toggle={toggle} />);
  const card = screen.getByRole("button", { name: /交易金额/ });
  expect(card.textContent).toContain("¥1,000");
  expect(card.textContent).toContain("同比 +1,900.0%");
  expect(card.textContent).toContain("去年同期 ¥50");
  expect(card.textContent).not.toContain("同款同比");
  expect(card.textContent).not.toContain("本期¥");
  expect(card.textContent).toContain("环比 前一期暂无经营记录");
  expect(card.textContent).not.toContain("去年同期（可比）");
  expect(card.textContent).not.toContain("无可比基数");
  expect(screen.queryByText(/比较用的“本期”数值可能不同/)).toBeNull();
  fireEvent.click(card);
  expect(toggle).toHaveBeenCalledWith("revenue");
  fireEvent.click(screen.getByText("比较说明与数据来源"));
  expect(screen.getByText(/没记录不当作0/)).toBeTruthy();
  expect(within(card).getByText("¥1,000")).toBeTruthy();
  expect(screen.getByText("2026-10-16 至 2026-10-31")).toBeTruthy();
  expect(screen.getByText("2025-10-16 至 2025-10-31")).toBeTruthy();
  expect(screen.getByText("2026-09-30 至 2026-10-15")).toBeTruthy();
  rerender(
    <Metrics
      data={{
        ...data,
        comparison_periods: {
          current: { start: "2028-02-29", end: "2028-02-29" },
          yoy: null,
          mom: { start: "2028-02-28", end: "2028-02-28" },
        },
      }}
      active={["revenue"]}
      toggle={toggle}
    />,
  );
  expect(screen.queryByText("2026-10-16 至 2026-10-31")).toBeNull();
  expect(screen.getByText("无对应日期")).toBeTruthy();
  rerender(
    <Metrics
      data={{
        ...data,
        metrics: {
          revenue: {
            ...data.metrics.revenue,
            status: "partial",
            observation_note: "本期有些日期没有店铺或商品经营记录，按已有记录统计。",
          },
        },
      }}
      active={["revenue"]}
      toggle={toggle}
    />,
  );
  expect(
    screen.getByText("本期有些日期没有店铺或商品经营记录，按已有记录统计。").closest("details"),
  ).toBeTruthy();
});
