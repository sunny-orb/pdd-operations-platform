// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Brief } from "./Overview";
import type { Board } from "./contracts";

afterEach(cleanup);
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
