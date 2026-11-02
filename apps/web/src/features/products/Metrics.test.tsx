// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Metrics } from "./Overview";
import type { Board } from "./contracts";

afterEach(cleanup);

it("keeps total distinct from paired values, explains empty comparisons and preserves selection", () => {
  const pair = {
    value: "100",
    previous: "50",
    change: "1",
    reason: null,
    message: null,
    matched_product_days: 1,
    matched_products: 1,
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
        yoy: "1",
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
          reason: "no_pairs",
          message: "暂无可比记录",
          matched_product_days: 0,
          matched_products: 0,
        },
      },
    },
  } as unknown as Board;
  const toggle = vi.fn();
  const { rerender } = render(<Metrics data={data} active={["revenue"]} toggle={toggle} />);
  const card = screen.getByRole("button", { name: /交易金额/ });
  expect(card.textContent).toContain("¥1,000");
  expect(card.textContent).toContain("同款同比 +100.0%");
  expect(card.textContent).toContain("本期¥100.00");
  expect(card.textContent).toContain("去年¥50.00");
  expect(card.textContent).toContain("同款环比 暂无可比记录");
  expect(card.textContent).not.toContain("去年同期（可比）");
  expect(card.textContent).not.toContain("无可比基数");
  expect(screen.getByText(/上方数值统计筛选后的全部商品/)).toBeTruthy();
  fireEvent.click(card);
  expect(toggle).toHaveBeenCalledWith("revenue");
  fireEvent.click(screen.getByText("比较说明与数据来源"));
  expect(screen.getByText(/不代表每天都有可比记录/)).toBeTruthy();
  expect(within(card).getByText("¥100.00")).toBeTruthy();
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
});
