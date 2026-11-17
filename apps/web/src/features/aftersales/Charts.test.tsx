// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { Charts } from "./Charts";

vi.mock("recharts", async (importOriginal) => {
  const recharts = await importOriginal<typeof import("recharts")>();
  return {
    ...recharts,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => (
      <recharts.ResponsiveContainer width={620} height={282}>
        {children}
      </recharts.ResponsiveContainer>
    ),
  };
});

afterEach(cleanup);

it("同名跨年报告的柱子仍直接显示各自完整的起止日期", () => {
  render(
    <Charts
      data={{
        reasons: [],
        metrics: [],
        trend: ["2025", "2026"].map((year) => ({
          id: year,
          label: "近30天退货分析 · 周报",
          period_start: `${year}-12-15`,
          period_end: `${Number(year) + 1}-01-13`,
          qty: "80",
        })),
      }}
    />,
  );

  for (const date of ["2025-12-15", "2026-01-13", "2026-12-15", "2027-01-13"]) {
    expect(screen.getByText(new RegExp(date))).toBeTruthy();
  }
});

it("原因图例默认展示所有显示占比达到 0.3% 的原因", async () => {
  const user = userEvent.setup();
  render(
    <Charts
      data={{
        trend: [],
        metrics: [],
        reasons: [
          { name: "不想要了", qty: "9769", share: "0.9769" },
          ...Array.from({ length: 6 }, (_, index) => ({
            name: `原因 ${index + 1}`,
            qty: "30",
            share: "0.003",
          })),
          { name: "显示为 0.3%", qty: "26", share: "0.0026" },
          { name: "显示为 0.2%", qty: "24", share: "0.0024" },
          { name: "低频原因", qty: "1", share: "0.0001" },
        ],
      }}
    />,
  );

  const legend = screen.getByRole("list", { name: "原因占比" });
  expect(within(legend).getAllByRole("listitem")).toHaveLength(8);
  expect(within(legend).getByText("显示为 0.3%")).toBeTruthy();
  expect(within(legend).queryByText("显示为 0.2%")).toBeNull();
  expect(within(legend).queryByText("低频原因")).toBeNull();

  await user.click(screen.getByRole("button", { name: "展开其余 2 项" }));
  expect(within(legend).getAllByRole("listitem")).toHaveLength(10);
  expect(within(legend).getByText("低频原因").closest("li")?.textContent).toContain("<0.1%");

  await user.click(screen.getByRole("button", { name: "收起原因" }));
  expect(within(legend).getAllByRole("listitem")).toHaveLength(8);
});
