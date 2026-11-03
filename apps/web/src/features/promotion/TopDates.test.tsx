// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Overview } from "./contracts";
import { TopDates } from "./Overview";

afterEach(cleanup);
function data(partial = true): Overview {
  return {
    metrics: {},
    net: {},
    series: [],
    funnel: [],
    windows: [],
    channel_summaries: [],
    coverage: {},
    current_record_count: "3",
    dataset_record_count: "3",
    top5: [
      {
        date: "2026-10-08",
        roi: "7.447",
        spend: "32145.70",
        revenue: "240040.22",
        roi_status: partial ? "partial" : "observed",
        roi_exclusions: {
          missing_spend_rows: partial ? 1 : 0,
          missing_revenue_rows: 0,
          revenue_without_spend: partial ? "650.20" : "0",
        },
      },
    ],
  };
}

it("只展示服务端可排名日期，筛选后清除旧结果并区分无记录", () => {
  const current = data(false);
  const { rerender } = render(<TopDates data={current} />);
  expect(screen.getByText("投产比（ROI） 7.45")).toBeTruthy();
  expect(screen.getByText("交易额 ¥240,040")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<TopDates data={{ ...current, top5: [] }} />);
  expect(screen.queryByText("2026-10-08")).toBeNull();
  expect(screen.getByText("当前范围没有可计算投产比的日期")).toBeTruthy();
  rerender(<TopDates data={{ ...current, top5: [], current_record_count: "0" }} />);
  expect(screen.getByText("当前范围暂无推广数据")).toBeTruthy();
  rerender(<TopDates data={undefined} />);
  expect(screen.queryByText(/暂无/)).toBeNull();
});
