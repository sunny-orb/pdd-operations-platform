// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Board } from "./contracts";
import { ProductFunnel } from "./ProductFunnel";

afterEach(cleanup);
const data = {
  scope: { start: "2026-10-02", end: "2026-11-01" },
  funnel: ["商品浏览量", "商品访客", "累计新增收藏量（未扣取消）", "支付买家", "支付件数"].map(
    (label, i) => ({
      label,
      value: String((i + 1) * 100),
      status: "observed",
      reason: null,
      valid_rows: "20",
      observed_rows: "20",
    }),
  ),
} as unknown as Board;

it("五项显示实际含义、同一响应日期及数值，不把浏览量或收藏冒充其他指标", () => {
  const { rerender } = render(<ProductFunnel data={data} />);
  const card = screen.getByRole("region", { name: "商品流量链路" });
  expect(within(card).getByText("2026-10-02 至 2026-11-01")).toBeTruthy();
  expect(within(card).getByText("商品浏览量")).toBeTruthy();
  expect(within(card).getByText("浏览次数")).toBeTruthy();
  expect(within(card).getByText("未扣取消收藏")).toBeTruthy();
  expect(within(card).queryByText(/未知|待验证|曝光|加购/)).toBeNull();
  const next = structuredClone(data);
  next.scope.start = "2026-10-30";
  next.funnel[0].value = "12345";
  rerender(<ProductFunnel data={next} />);
  expect(within(card).getByText("12,345")).toBeTruthy();
  expect(within(card).getByText("2026-10-30 至 2026-11-01")).toBeTruthy();
});

it("真实0照常显示；空值与空字段合计单独提示，不把缺经营日当成技术异常", () => {
  const next = structuredClone(data);
  Object.assign(next.funnel[0], { value: "0" });
  Object.assign(next.funnel[1], { status: "partial", valid_rows: "20", observed_rows: "20" });
  Object.assign(next.funnel[2], { status: "partial", valid_rows: "19", observed_rows: "20" });
  Object.assign(next.funnel[3], {
    value: null,
    status: "missing",
    valid_rows: "0",
    observed_rows: "20",
  });
  Object.assign(next.funnel[4], {
    value: null,
    status: "missing",
    valid_rows: "0",
    observed_rows: "0",
  });
  render(<ProductFunnel data={next} />);
  const card = screen.getByRole("region", { name: "商品流量链路" });
  expect(within(card).getByText("0")).toBeTruthy();
  expect(within(card).getAllByText("已知部分")).toHaveLength(1);
  expect(within(card).getByText("暂无可用数值")).toBeTruthy();
  expect(within(card).getByText("暂无经营记录")).toBeTruthy();
  expect(within(card).getAllByText("—")).toHaveLength(2);
});
