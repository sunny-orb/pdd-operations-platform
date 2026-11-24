// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BusinessReportView } from "./BusinessReport";
import type { BusinessReport } from "./contracts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const report: BusinessReport = {
  kind: "dashboard",
  domain: "sales",
  capability: "ratings",
  title: "店铺评分",
  source: "已发布评分",
  period: null,
  scope: {},
  data_version: "v1",
  versions: { sales: "v1" },
  facts: [
    {
      key: "score",
      label: "评分",
      value: "95.50",
      unit: "number",
      status: "available",
      reason: null,
    },
    {
      key: "change",
      label: "变化（pp）",
      value: "0.24",
      unit: "number",
      status: "available",
      reason: null,
    },
    {
      key: "small",
      label: "微小变化",
      value: "1e-7",
      unit: "number",
      status: "available",
      reason: null,
    },
    { key: "zero", label: "真实零", value: "0", unit: "number", status: "available", reason: null },
    {
      key: "missing",
      label: "缺项",
      value: null,
      unit: "number",
      status: "unavailable",
      reason: "来源未提供",
    },
  ],
  tables: [
    {
      title: "店铺明细",
      columns: [
        { key: "score", label: "评分", unit: "number" },
        { key: "listing_id", label: "内部键", unit: "text" },
      ],
      rows: [{ score: "95.50", listing_id: "s/A" }, { score: null }],
      offset: 20,
      total: 53,
    },
  ],
  notes: ["评分沿用同版规则"],
  series: [
    {
      key: "score",
      label: "评分",
      unit: "number",
      points: [
        { date: "2026-11-20", value: "95.5" },
        { date: "2026-11-21", value: null },
        { date: "2026-11-22", value: "94" },
      ],
    },
  ],
};

it("保留评分、pp与微小数的小数位，真实零与NULL分别显示", () => {
  render(<BusinessReportView report={report} showChart={false} />);
  fireEvent.click(screen.getByText("查看指标与数据依据"));
  const facts = screen.getAllByRole("table")[0];
  for (const [label, expected] of [
    ["评分", "95.5"],
    ["变化（pp）", "0.24"],
    ["微小变化", "0.0000001"],
    ["真实零", "0"],
    ["缺项", "—"],
  ]) {
    const row = within(facts).getByText(label, { exact: true }).closest("tr")!;
    expect(within(row).getByRole("cell").textContent).toBe(expected);
  }
  expect(within(facts).getByText("来源未提供")).toBeTruthy();
});

it("分页明细只报告已返回范围与总体条数，隐藏内部链接键", () => {
  render(<BusinessReportView report={report} showChart={false} />);
  fireEvent.click(screen.getByText("查看指标与数据依据"));
  expect(screen.getByText("显示第21—22条，共53条")).toBeTruthy();
  const table = screen.getByRole("table", { name: "店铺明细" });
  expect(within(table).getAllByRole("row")).toHaveLength(3);
  expect(within(table).queryByText("内部键")).toBeNull();
  expect(within(table).getByText("95.5")).toBeTruthy();
  expect(within(table).getByText("—")).toBeTruthy();
  expect(screen.queryByRole("img", { name: "评分趋势" })).toBeNull();
});

it("空分页不补零或展示伪造行，来源及版本仍可查看", () => {
  render(
    <BusinessReportView
      report={{
        ...report,
        facts: [],
        tables: [{ ...report.tables[0], rows: [], offset: 0, total: 0 }],
      }}
      showChart={false}
    />,
  );
  fireEvent.click(screen.getByText("查看指标与数据依据"));
  expect(screen.getByText("当前没有匹配记录，共0条")).toBeTruthy();
  expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(0);
  fireEvent.click(screen.getByText("来源与口径"));
  expect(screen.getByText("已发布评分")).toBeTruthy();
  expect(screen.getByText("数据版本 v1")).toBeTruthy();
});

it("按需展示分组趋势并能切换指标", () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  render(
    <BusinessReportView
      report={{
        ...report,
        series: [
          ...report.series,
          {
            key: "rate",
            label: "退款率",
            unit: "percent",
            points: [
              { date: "2026-11-20", value: "0" },
              { date: "2026-11-21", value: null },
              { date: "2026-11-22", value: "0.1" },
            ],
          },
        ],
      }}
      showChart
    />,
  );
  expect(screen.getByRole("img", { name: "评分趋势" })).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "趋势指标" }), {
    target: { value: "rate" },
  });
  expect(screen.getByRole("img", { name: "退款率趋势" })).toBeTruthy();
  expect(screen.queryByRole("img", { name: "评分趋势" })).toBeNull();
});
