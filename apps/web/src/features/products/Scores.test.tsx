// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Scores } from "./Scores";
import type { ProductsState } from "./data";
import { readScope } from "./scope";

vi.mock("./Filters", () => ({ Filters: () => null }));
const resource = vi.hoisted(() => ({
  data: {
    status: "trial_active",
    total: 1,
    total_count: 1,
    scored_count: 1,
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
  isPlaceholderData: false,
  refetch: vi.fn(),
}));
vi.mock("./data", () => ({ useResource: () => resource }));

afterEach(cleanup);
beforeEach(() => {
  resource.isPlaceholderData = false;
  vi.clearAllMocks();
  resource.data.total = 1;
  resource.data.total_count = 1;
  resource.data.scored_count = 1;
  resource.data.end = "2026-10-26";
  resource.data.groups = { complete: 0, pending: 1, unscored: 0 };
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
});
const state = {
  metadata: { data: { date_from: "2026-08-28", date_to: "2026-10-26" } },
  version: "v",
  change: vi.fn(),
} as unknown as ProductsState;
function renderScores() {
  return render(
    <Scores
      state={state}
      scope={readScope(new URLSearchParams(), "score", "2026-08-28", "2026-10-26")}
    />,
  );
}

it("shows enabled trial partial points and pending weight without claiming complete or formal scaling", () => {
  renderScores();
  expect(screen.getByText(/已计分 42.5 分/)).toBeTruthy();
  expect(screen.getByText(/待验证权重 24/)).toBeTruthy();
  expect(screen.getByText(/当前仍显示此前试行结果，原版评分标准尚未接入/)).toBeTruthy();
  expect(screen.queryByText("暂未评分 · 缺少相容四档标准")).toBeNull();
  expect(screen.getByText(/推广沿来源支付日汇总/)).toBeTruthy();
  expect(screen.queryByText(/推广归因窗口及完整日待验证/)).toBeNull();
});

it("shows filtered and all product counts while keeping the online full-list computed count", () => {
  resource.data.total = 37;
  resource.data.total_count = 50;
  resource.data.scored_count = 13;
  resource.data.groups = { complete: 2, pending: 9, unscored: 26 };
  resource.data.end = "2026-10-31";
  const { rerender } = renderScores();
  const summary = () => within(screen.getByRole("region", { name: "新品评分概况" }));
  expect(summary().getByText("37 / 50 款新品")).toBeTruthy();
  expect(summary().getByText("已计算 13 款 · 评分截至：2026-10-31")).toBeTruthy();

  resource.data.total = 4;
  resource.data.groups = { complete: 0, pending: 0, unscored: 4 };
  resource.data.end = "2026-11-01";
  rerender(
    <Scores
      state={state}
      scope={readScope(new URLSearchParams(), "score", "2026-08-28", "2026-11-01")}
    />,
  );
  expect(summary().getByText("4 / 50 款新品")).toBeTruthy();
  expect(summary().getByText("已计算 13 款 · 评分截至：2026-11-01")).toBeTruthy();
});

it("翻页等待时保留卡片和分页、暂停操作，完成后只重置卡片内部滚动", () => {
  resource.data.total = 37;
  const scope = readScope(new URLSearchParams(), "score", "2026-08-28", "2026-10-26");
  const { container, rerender } = render(<Scores state={state} scope={scope} />);
  const grid = container.querySelector<HTMLDivElement>(".new-potential-grid")!;
  const pager = container.querySelector(".ant-pagination")!;
  grid.scrollTop = 123;
  resource.isPlaceholderData = true;
  rerender(<Scores state={state} scope={{ ...scope, page: 2 }} />);
  expect(container.querySelector(".new-potential-grid")).toBe(grid);
  expect(container.querySelector(".ant-pagination")).toBe(pager);
  expect(grid.scrollTop).toBe(123);
  expect(grid.hasAttribute("inert")).toBe(true);
  expect(grid.getAttribute("aria-busy")).toBe("true");
  const next = within(pager as HTMLElement).getByRole("button", { name: "right" });
  fireEvent.click(next);
  expect(state.change).not.toHaveBeenCalled();

  resource.isPlaceholderData = false;
  rerender(<Scores state={state} scope={{ ...scope, page: 2 }} />);
  expect(grid.scrollTop).toBe(0);
  expect(grid.hasAttribute("inert")).toBe(false);
  fireEvent.click(next);
  expect(state.change).toHaveBeenCalledWith("score", { page: 3 });
});
