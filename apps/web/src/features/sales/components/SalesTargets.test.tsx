// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SalesTargets } from "./SalesTargets";

vi.mock("../api/details", () => ({
  detailPath: () => "targets",
  targetsSchema: {},
  useSalesDetail: () => ({
    data: {
      data: {
        month_actual: "100",
        annual_actual: "1000",
        month_target: "25000",
        annual_target: "300000",
        month_ratio: "0.004",
        annual_ratio: "0.003333333333",
        gap: "24900",
        state: "partial",
        month_missing_days: 1,
        annual_missing_days: 2,
        target_basis: "base",
        target_basis_label: "基础目标",
        target_coverage: {
          available_months: Array.from({ length: 12 }, (_, n) => String(n)),
          missing_months: [],
        },
        note: "销售缺店铺日，达成率仅为已观察参考。",
      },
    },
    error: null,
    retry: vi.fn(),
  }),
}));
afterEach(cleanup);
it("实际目标输入进入原三卡，标基础基准与覆盖且不再固定称断源", () => {
  render(<SalesTargets month="2026-10" version="v" onConflict={vi.fn()} />);
  expect(screen.getAllByText(/基础目标/).length).toBeGreaterThan(0);
  expect(screen.getByText(/12\/12/)).toBeTruthy();
  expect(screen.getByText(/已观察参考/)).toBeTruthy();
  expect(screen.queryByText(/目标原入口当前不可用/)).toBeNull();
  expect(screen.getByRole("link", { name: /查看基础目标来源/ }).getAttribute("href")).toContain(
    "G1dNbVRapaTGlwsL9QbcNKBmnSd",
  );
});
