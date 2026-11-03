// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Board } from "./contracts";
import { Risks } from "./Risks";

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
function board(count: number): Board {
  return {
    scope: { start: "2026-10-02", end: "2026-11-01" },
    risks: Array.from({ length: count }, (_, i) => ({
      listing_id: `listing-${i}`,
      product_name: `商品${i + 1}`,
      product_id: `${i}`,
      level: "A级",
      shop_name: "一店",
      period_revenue: "1000",
      alert: {
        priority: "P0",
        label: "爆品严重流量下滑",
        current: "0",
        baseline: "56",
        change: "-1",
        type: "number",
        trend: ["50", "45", "40", "30", "20", "10", "0"],
        expected_loss: null,
        reasons: ["访客显著下滑"],
        action: "检查活动与素材。",
        deadline: null,
        status: "未启用",
        basis: "订单转化=订单÷访客",
      },
    })),
  } as unknown as Board;
}
function next() {
  fireEvent.click(screen.getByTitle("Next Page"));
}

it("完整清单按五条分页，末页保留剩余项，前后页不丢商品", () => {
  render(<Risks data={board(11)} />);
  expect(screen.getAllByRole("row")).toHaveLength(6);
  expect(screen.getByText("商品5")).toBeTruthy();
  expect(screen.queryByText("商品6")).toBeNull();
  next();
  expect(screen.getByText("商品6")).toBeTruthy();
  expect(screen.getByText("商品10")).toBeTruthy();
  next();
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("商品11")).toBeTruthy();
  fireEvent.click(screen.getByTitle("Previous Page"));
  expect(screen.getByText("商品6")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("共 11 款");
});

it("切换筛选结果回第一页，空结果不显示残留行", () => {
  const firstScope = board(11);
  const { rerender } = render(<Risks data={firstScope} />);
  next();
  rerender(<Risks data={board(8)} />);
  expect(screen.getByText("商品1")).toBeTruthy();
  expect(screen.queryByText("商品6")).toBeNull();
  rerender(<Risks data={firstScope} />);
  expect(screen.getByText("商品1")).toBeTruthy();
  expect(screen.queryByText("商品6")).toBeNull();
  rerender(<Risks data={board(0)} />);
  expect(screen.queryByText("商品1")).toBeNull();
  expect(screen.getByText("当前筛选范围没有达到风险条件的商品")).toBeTruthy();
});

it("保留真实零与空影响金额，隐藏未接入的处理列并标明趋势指标", () => {
  render(<Risks data={board(1)} />);
  const row = screen.getByRole("row", { name: /商品1/ });
  expect(within(row).getByText("0", { exact: true })).toBeTruthy();
  expect(within(row).getByText("-100.0%")).toBeTruthy();
  expect(within(row).getAllByText("—")).toHaveLength(1);
  expect(within(row).queryByText("待处理")).toBeNull();
  expect(screen.queryByRole("columnheader", { name: "截止时间" })).toBeNull();
  expect(screen.queryByRole("columnheader", { name: /^状态$/ })).toBeNull();
  expect(screen.getByRole("columnheader", { name: "估算影响金额" })).toBeTruthy();
  expect(within(row).getByText("商品访客数", { exact: true })).toBeTruthy();
  expect(screen.getByText(/观察日：2026-11-01；累计交易额：2026-10-02 至 2026-11-01/)).toBeTruthy();
});
