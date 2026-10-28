// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Scores } from "./Scores";
import type { ProductsState } from "./data";
import { readScope } from "./scope";

vi.mock("./Filters", () => ({ Filters: () => null }));
vi.mock("./data", () => ({
  useResource: () => ({
    data: {
      status: "trial_active",
      total: 1,
      end: "2026-10-26",
      groups: { complete: 0, pending: 1, unscored: 0 },
      items: [
        {
          listing_id: "a",
          product_id: "1",
          product_name: "试行款",
          category: "帽子",
          shop_id: "s",
          operator: null,
          score: "42.5",
          pending_weight: "24",
          complete_score: null,
          signals: [],
          decision_label: "先补验证 · 暂不放量",
          action: "新试行标准",
          coverage: "observed_reference",
          revenue: "10",
          visitors: "2",
          units: "1",
          ad_impressions: "2000",
          ad_roi: "2",
        },
      ],
    },
    error: null,
    isPending: false,
    refetch: vi.fn(),
  }),
}));

afterEach(cleanup);
it("shows enabled trial partial points and pending weight without claiming complete or formal scaling", () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  const state = {
    metadata: { data: { date_from: "2026-08-28", date_to: "2026-10-26" } },
    version: "v",
    change: vi.fn(),
  } as unknown as ProductsState;
  render(
    <Scores
      state={state}
      scope={readScope(new URLSearchParams(), "score", "2026-08-28", "2026-10-26")}
    />,
  );
  expect(screen.getByText(/已计分 42.5/)).toBeTruthy();
  expect(screen.getByText(/待验证权重 24/)).toBeTruthy();
  expect(screen.getAllByText(/新试行标准已启用/).length).toBeGreaterThan(0);
  expect(screen.queryByText("暂未评分 · 缺少相容四档标准")).toBeNull();
  expect(screen.getByText(/推广沿来源支付日汇总/)).toBeTruthy();
  expect(screen.queryByText(/推广归因窗口及完整日待验证/)).toBeNull();
});
