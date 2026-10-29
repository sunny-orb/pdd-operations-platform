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
