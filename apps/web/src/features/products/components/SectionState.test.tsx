// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SectionState, type QueryView } from "./SectionState";
const state = (computationState: "pending" | "failed", hasCompatibleResult = false) =>
  ({
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
    data: {
      data: { items: [] },
      availability: "not_connected",
      computationState,
      meta: {
        dataVersion: "v",
        dataThrough: null,
        lastSuccessAt: null,
        completeness: "unknown",
        missingReasons: [],
        isPreview: false,
        hasCompatibleResult,
      },
    },
  }) as unknown as QueryView<{ items: unknown[] }>;
afterEach(cleanup);
it("评分待生成优先于来源未接入，不伪造空评分", () => {
  render(<SectionState query={state("pending")}>{() => "旧评分"}</SectionState>);
  expect(screen.getByText("结果尚未生成")).toBeTruthy();
  expect(screen.queryByText("数据尚未接入")).toBeNull();
});
it("评分失败明确提示而非误报未接入", () => {
  render(<SectionState query={state("failed")}>{() => "旧评分"}</SectionState>);
  expect(screen.getByText("分析暂不可用")).toBeTruthy();
  expect(screen.queryByText("旧评分")).toBeNull();
});

it("相容评分ready但最近尝试失败时保留结果并明确提示", () => {
  const query = state("failed", true);
  query.data!.availability = "ready";
  query.data!.computationState = "ready";
  Object.assign(query.data!.meta, {
    lastAttemptStatus: "failed",
    lastAttemptAt: "2026-10-23T01:00:00Z",
    lastError: { code: "SCORE_FAILED", message: "合成评分失败" },
  });
  render(<SectionState query={query}>{() => "相容评分"}</SectionState>);
  expect(screen.getByText("最近评分计算失败，保留上次相容结果")).toBeTruthy();
  expect(screen.getByText("相容评分")).toBeTruthy();
});
