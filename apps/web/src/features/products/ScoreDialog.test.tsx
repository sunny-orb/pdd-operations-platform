// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ScoreCard } from "./contracts";
import type { ProductsState } from "./data";
import { readScope } from "./scope";
import { ScoreDialog } from "./ScoreDialog";

const resource = vi.hoisted(() => ({
  data: null as ScoreCard | null,
  isPending: false,
  isSuccess: true,
  error: null as Error | null,
  refetch: vi.fn(),
}));
vi.mock("./data", () => ({ useResource: () => resource }));
const state = { version: "v" } as ProductsState;
const close = vi.fn();
const scope = readScope(new URLSearchParams(), "score", "2026-09-04", "2026-11-02");
const example: ScoreCard = {
  listing_id: "shop:product",
  shop_id: "shop",
  shop_name: "测试店铺",
  product_id: "00123",
  product_name: "示例新品",
  category: "羽绒服",
  level: "新品",
  operator: "测试运营",
  field_status: {},
  start: "2026-10-27",
  end: "2026-11-02",
  decision: "pending",
  decision_label: "先补验证 · 暂不放量",
  action: "暂不增加预算",
  score: "13.5",
  pending_weight: "14",
  complete_score: null,
  season: null,
  lifecycle: null,
  revenue: "3122.12",
  visitors: "1000",
  units: "20",
  ad_impressions: null,
  ad_roi: null,
  coverage: "partial",
  reason: "原版参数尚未接入",
  signals: [
    {
      code: "revenue",
      label: "交易额",
      weight: 18,
      type: "currency",
      value: "3122.12",
      basic: "0",
      good: "10.5",
      excellent: "100",
      outstanding: "5000",
      grade: "优秀",
      points: "13.5",
      status: "observed_reference",
      reason: "试行参照",
      reference_group: "羽绒服",
      sample_count: "23",
    },
    {
      code: "growth",
      label: "近7日交易倍数",
      weight: 14,
      type: "ratio",
      value: "1.97",
      basic: null,
      good: null,
      excellent: null,
      outstanding: null,
      grade: null,
      points: null,
      status: "pending",
      reason: "增长历史参照未接入",
      reference_group: null,
      sample_count: null,
    },
  ],
};
beforeEach(() => {
  vi.clearAllMocks();
  resource.data = structuredClone(example);
  resource.error = null;
  resource.isPending = false;
  resource.isSuccess = true;
});
afterEach(cleanup);
function renderDialog() {
  return render(
    <div className="m1-products-scope">
      <ScoreDialog state={state} selected="shop:product" scope={scope} close={close} />
    </div>,
  );
}

it("紧凑布局保留真实商品身份、日期和部分得分，不冒充完整评分", () => {
  renderDialog();
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText(/商品ID：00123/).textContent).toContain("测试店铺");
  expect(dialog.getByText(/评分期间：2026-10-27 至 2026-11-02/)).toBeTruthy();
  expect(dialog.getByText("13.5 分")).toBeTruthy();
  expect(dialog.queryByText("已验证得分")).toBeNull();
  expect(dialog.getByText("缺项不折算成满分")).toBeTruthy();
  const table = within(dialog.getByRole("table"));
  const rows = table.getAllByRole("row");
  expect(table.getAllByRole("columnheader")).toHaveLength(9);
  expect(within(rows[1]).getByText("¥3,122.12")).toBeTruthy();
  expect(within(rows[1]).getByText("13.5 / 18")).toBeTruthy();
  expect(within(rows[1]).getByText("¥0")).toBeTruthy();
  expect(within(rows[2]).getAllByText("—")).toHaveLength(5);
  expect(within(rows[2]).getByText("暂未计分")).toBeTruthy();
  const details = dialog.getByText("评分说明").closest("details")!;
  expect(details.open).toBe(false);
  fireEvent.click(within(details).getByText("评分说明"));
  expect(details.open).toBe(true);
  expect(within(details).getByText("增长历史参照未接入")).toBeTruthy();
  fireEvent.click(dialog.getByRole("button", { name: "关闭" }));
  expect(close).toHaveBeenCalledOnce();
});

it("完整评分、有效零分及档位都跟随接口，不由前端重新评分", () => {
  resource.data!.complete_score = "75.5";
  resource.data!.score = "75.5";
  resource.data!.pending_weight = "0";
  resource.data!.signals[1].points = "0";
  resource.data!.signals[1].grade = "未达基础";
  renderDialog();
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("75.5 / 100")).toBeTruthy();
  expect(dialog.getByText("0 / 14")).toBeTruthy();
  expect(dialog.getByText("未达基础")).toBeTruthy();
  expect(dialog.queryByText("缺项不折算成满分")).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "关闭评分详情" }));
  expect(close).toHaveBeenCalledOnce();
});

it("没有详情时不生成摘要数字，仍能关闭弹窗", () => {
  resource.data = null;
  renderDialog();
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("当前版本没有该新品对象，请关闭后重新筛选")).toBeTruthy();
  expect(dialog.queryByRole("table")).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "关闭" }));
  expect(close).toHaveBeenCalledOnce();
});

it("全部未计分时保留空分，不把缺项或真实输入改成零分", () => {
  resource.data!.score = null;
  resource.data!.pending_weight = "32";
  for (const signal of resource.data!.signals) {
    signal.points = null;
    signal.grade = null;
  }
  renderDialog();
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("— 分")).toBeTruthy();
  expect(dialog.getByText("32 分")).toBeTruthy();
  expect(dialog.getByText("0 / 0 项")).toBeTruthy();
  expect(dialog.getAllByText("暂未计分")).toHaveLength(2);
  expect(dialog.queryByText("0 / 18")).toBeNull();
  expect(dialog.getByText("¥3,122.12")).toBeTruthy();
});
