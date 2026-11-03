// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Brief } from "./Overview";
import type { Board } from "./contracts";

afterEach(cleanup);

it("shows the category's specific comparison reason alongside dynamic revenue", () => {
  const data = {
    scope: { start: "2026-09-28", end: "2026-10-28" },
    brief: {
      start: "2026-10-22",
      end: "2026-10-28",
      categories: [
        {
          category: "帽子",
          revenue: "810547.64",
          growth: null,
          comparison_reason: "两期品类日记录不足",
        },
      ],
      products: [],
      risks: [],
      rising: [],
    },
  } as unknown as Board;
  render(<Brief data={data} />);
  const row = screen.getByRole("button", { name: /帽子/ });
  expect(row.textContent).toContain("交易 ¥810,548");
  expect(row.textContent).toContain("两期品类日记录不足");
  expect(row.textContent).not.toContain("无可比基数");
});

it.each([
  ["2026-09-28", "2026-10-28", "近31天"],
  ["2026-12-15", "2027-01-14", "近31天"],
  ["2028-02-01", "2028-03-02", "近31天"],
  ["2026-10-28", "2026-10-28", "近1天"],
])("labels the actual calendar window %s to %s", (start, end, label) => {
  const data = {
    observed_rows: "43216",
    scope: { start, end },
  } as unknown as Board;
  const { container } = render(<Brief data={data} />);
  const bar = container.querySelector(".scope-bar")!;
  expect(bar.textContent).toContain(`${label}（${start} 至 ${end}）`);
  expect(bar.textContent).not.toContain("所选期");
  expect(bar.textContent).not.toContain("按当前资料归属");
});

it("lets a ranked product locate its full listing in the independent overview scope", () => {
  const onSelect = vi.fn();
  const data = {
    observed_rows: "1",
    scope: { start: "2026-10-20", end: "2026-10-26" },
    brief: {
      start: "2026-10-20",
      end: "2026-10-26",
      categories: [],
      risks: [],
      rising: [],
      products: [
        {
          listing_id: "shop-a-full-key",
          product_name: "测试商品",
          product_id: "123",
          shop_name: "店A",
          revenue: "10",
          growth: null,
        },
      ],
    },
  } as unknown as Board;
  render(<Brief data={data} onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: /测试商品/ }));
  expect(onSelect).toHaveBeenCalledWith({ products: ["shop-a-full-key"] });
});

function adviceBoard(name: string, status = "observed_reference"): Board {
  return {
    observed_rows: "14",
    scope: { start: "2026-10-01", end: "2026-10-30" },
    brief: {
      start: "2026-10-24",
      end: "2026-10-30",
      previous_start: "2026-10-17",
      categories: [],
      products: [],
      risks: [],
      rising: [],
      actions: {
        generation_method: "rules",
        ai_status: "not_connected",
        as_of_date: "2026-10-30",
        status,
        message:
          status === "no_match" ? "当前筛选范围未触发现有规则提醒，继续观察。" : "部分数据不足。",
        items: name
          ? [
              {
                kind: "risk",
                listing_ids: ["risk-id"],
                text: `优先处理「${name}」：流量下滑先检查活动、素材与流量入口。`,
              },
              {
                kind: "opportunity",
                listing_ids: ["new-id"],
                text: `对「${name}新品」补充素材与关联推荐，分阶段放大有效流量并观察转化承接。`,
              },
            ]
          : [],
        notices:
          status === "insufficient_data"
            ? ["商品甲在2026-10-29缺少支付订单数据，暂不能判断这一项。"]
            : [],
      },
    },
  } as unknown as Board;
}

it("renders the two online paragraphs without expanding per-product figures", () => {
  render(<Brief data={adviceBoard("商品甲")} />);
  expect(screen.getByRole("region", { name: "建议动作" })).toBeTruthy();
  expect(screen.getByText("规则提醒")).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByText(/优先处理「商品甲」/)).toBeTruthy();
  expect(screen.getByText(/对「商品甲新品」补充素材与关联推荐/)).toBeTruthy();
  expect(screen.queryByText(/AI尚未接入/)).toBeNull();
});

it("replaces both paragraphs when the selected scope changes", () => {
  const { rerender } = render(<Brief data={adviceBoard("商品甲")} />);
  rerender(<Brief data={adviceBoard("商品乙")} />);
  expect(screen.queryByText(/商品甲/)).toBeNull();
  expect(screen.getByText(/优先处理「商品乙」/)).toBeTruthy();
  expect(screen.getByText(/对「商品乙新品」/)).toBeTruthy();
});

it("keeps both dynamic suggestions and omits per-product diagnostic notices", () => {
  const data = adviceBoard("商品甲");
  data.brief.actions.notices = ["商品乙在2026-10-29暂无日经营记录。"];
  const { rerender } = render(<Brief data={data} />);
  expect(screen.getByText(/优先处理「商品甲」/)).toBeTruthy();
  expect(screen.getByText(/对「商品甲新品」/)).toBeTruthy();
  expect(screen.queryByText("数据说明")).toBeNull();
  expect(screen.queryByText(/商品乙在2026-10-29/)).toBeNull();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  rerender(<Brief data={adviceBoard("", "no_match")} />);
  expect(screen.getByText(/未触发现有规则提醒/)).toBeTruthy();
});
