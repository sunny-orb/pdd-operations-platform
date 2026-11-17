// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Details } from "./Details";
import { Metrics } from "./Metrics";
import { Analysis } from "./Analysis";
import type { Meta } from "./contracts";
import type { AftersalesState } from "./data";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {} }));
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

const meta: Meta = {
  data_version: "v1",
  status: "partial",
  default_report_id: "r1",
  observed_to: "2026-11-16",
  complete_through: null,
  last_success_at: "2026-11-17T00:00:00Z",
  last_attempt_at: "2026-11-17T00:00:00Z",
  sources: ["售后", "已发货", "已取消"].map((label) => ({
    code: label,
    label,
    locator: "public.aftersales_reports_rpa",
    status: "observed",
    observed_from: "2026-10-18",
    observed_to: "2026-11-16",
    last_success_at: "2026-11-17T00:00:00Z",
    last_attempt_at: "2026-11-17T00:00:00Z",
    error_code: null,
    note: "同一来源的必要口径，原行不是订单数。",
  })),
};

it("来源口径并入现有说明，共用口径只保留一次且默认折叠", async () => {
  const user = userEvent.setup();
  render(<Details data={undefined} meta={meta} />);
  expect(screen.queryByText("来源与更新状态")).toBeNull();
  expect(screen.getAllByText("同一来源的必要口径，原行不是订单数。")).toHaveLength(1);
  const note = screen.getByText("同一来源的必要口径，原行不是订单数。");
  expect(note.closest("details")?.open).toBe(false);
  await user.click(screen.getByText("数据与计算口径"));
  expect(note.closest("details")?.open).toBe(true);
});

it("指标保留数值和缺失状态，计算说明移到可展开的指标口径", async () => {
  const user = userEvent.setup();
  render(
    <Metrics
      variant="risk"
      metrics={[
        {
          key: "total_rate",
          label: "总退款率",
          type: "percent",
          value: "0.4",
          status: "observed_reference",
          detail: "报告统计范围说明",
          note: "取消与入库金额合计除以GMV",
        },
        {
          key: "total_amount",
          label: "退款金额",
          type: "currency",
          value: null,
          status: "source_unavailable",
          detail: "缺少金额来源",
          note: "",
        },
      ]}
    />,
  );
  expect(screen.getByText("40.0%")).toBeTruthy();
  expect(screen.queryByText("本期数据")).toBeNull();
  const note = screen.getByText("取消与入库金额合计除以GMV");
  expect(note.closest("details")?.open).toBe(false);
  expect(screen.getByText("缺少金额来源").closest("details")?.open).toBe(false);
  await user.click(screen.getByText("指标口径"));
  expect(note.closest("details")?.open).toBe(true);
});

it("表格保留商品规格和真实零，逐行口径按需展开且不遗漏", async () => {
  const user = userEvent.setup();
  const data = {
    status: "partial",
    scope_note: "缺行不补零",
    total: 1,
    page: 1,
    size: 25,
    columns: [
      { key: "product", label: "商品", type: "text" },
      { key: "qty", label: "实退数量", type: "number" },
      { key: "quality", label: "质量", type: "list" },
    ],
    rows: [
      {
        id: "row1",
        cells: {
          product: { value: "童帽", detail: "蓝色 100码" },
          qty: { value: "0", detail: "实退数量不是订单数" },
          quality: { value: null, detail: "样本不足不能判断质量趋势" },
        },
      },
    ],
  };
  render(
    <Analysis
      state={
        {
          scope: { view: "category", page: 1, size: 25 },
          analysis: { data: { data }, isFetching: false },
          change: vi.fn(),
        } as unknown as AftersalesState
      }
    />,
  );
  expect(screen.getByText("蓝色 100码").closest("details")).toBeNull();
  expect(screen.getByText("0")).toBeTruthy();
  expect(screen.queryByText("实退数量不是订单数")).toBeNull();
  expect(screen.getByText("缺行不补零").closest("details")?.open).toBe(false);
  await user.click(screen.getByRole("button", { name: "展开行说明" }));
  expect(screen.getByText("实退数量不是订单数")).toBeTruthy();
  expect(screen.getByText("样本不足不能判断质量趋势")).toBeTruthy();
});
