// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SalesTargets } from "./SalesTargets";

const { useSalesDetail } = vi.hoisted(() => ({ useSalesDetail: vi.fn() }));
vi.mock("../api/details", () => ({
  detailPath: () => "targets",
  targetsSchema: {},
  useSalesDetail,
}));
const target = {
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
  target_basis: "original_gmv",
  target_basis_label: "原版 GMV 目标",
  target_coverage: {
    available_months: Array.from({ length: 12 }, (_, n) => String(n)),
    missing_months: [],
  },
  monthly_window: { start: "2026-10-01", end: "2026-10-29" },
  annual_window: { start: "2026-02-01", end: "2026-10-29" },
  note: "销售缺店铺日，达成率仅为已观察参考。",
};
beforeEach(() => {
  useSalesDetail.mockReturnValue({ data: { data: target }, error: null, retry: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("三卡默认紧凑，来源与覆盖说明折叠在卡片外，展开仍能查看", async () => {
  const { container } = render(<SalesTargets month="2026-10" version="v" onConflict={vi.fn()} />);
  const cards = container.querySelectorAll(".target-card");
  expect(cards).toHaveLength(3);
  const details = container.querySelector("details")!;
  expect(details.open).toBe(false);
  expect(details.closest(".target-card")).toBeNull();
  expect(within(cards[0] as HTMLElement).getByText("目标跟踪")).toBeTruthy();
  expect(within(cards[1] as HTMLElement).getByText("年度累计")).toBeTruthy();
  expect(within(cards[0] as HTMLElement).getByText("0.40%")).toBeTruthy();
  expect(within(cards[1] as HTMLElement).getByText("0.33%")).toBeTruthy();
  expect(cards[2].textContent).not.toMatch(/12\/12|数仓原值|固定六店|原版 GMV 目标/);
  expect(within(cards[2] as HTMLElement).getAllByRole("link")).toHaveLength(1);
  expect(screen.getByText(/12\/12/).closest("details")).toBe(details);
  expect(container.textContent).not.toMatch(/仅供参考|已观察参考|待补齐/);
  expect(cards[0].textContent).toContain("¥100");
  expect(cards[2].textContent).toContain("¥24,900");
  expect(screen.getByText(/缺少 1 条店铺日销售记录/).closest("details")).toBe(details);
  expect(screen.getByText(/缺少 2 条店铺日销售记录/).closest("details")).toBe(details);
  await userEvent.click(screen.getByText("查看目标口径与数据说明"));
  expect(details.open).toBe(true);
  expect(
    screen.getByRole("link", { name: /查看原版 GMV 目标来源/ }).getAttribute("href"),
  ).toContain("XGzTbd9xJaASV3soYnycMmf0nY3");
  expect(details.textContent).toContain("2026-10-01 至 2026-10-29");
});

it("接口更新后金额和两位完成率随之变化，超额进度封顶但百分比保留，缺值不补零", () => {
  const { rerender, container } = render(
    <SalesTargets month="2026-10" version="v" onConflict={vi.fn()} />,
  );
  useSalesDetail.mockReturnValue({
    data: {
      data: {
        ...target,
        month_actual: "26255.42",
        month_ratio: "1.0502168",
        gap: "0",
        month_missing_days: 0,
        monthly_window: { start: "2026-11-01", end: "2026-11-01" },
      },
    },
    error: null,
    retry: vi.fn(),
  });
  rerender(<SalesTargets month="2026-11" version="v2" onConflict={vi.fn()} />);
  expect(screen.getByText("¥26,255")).toBeTruthy();
  expect(screen.getByText("105.02%")).toBeTruthy();
  expect(screen.getAllByRole("progressbar")[0].getAttribute("aria-valuenow")).toBe("100");
  expect(container.querySelector(".target-summary")!.textContent).toContain("¥0");
  expect(container.textContent).not.toContain("2026-10-01");
  expect(container.textContent).toContain("2026-11-01 至 2026-11-01");
  expect(container.textContent).not.toContain("缺少 1 条店铺日销售记录");
  expect(container.textContent).toContain("各店每日均有销售记录");
  useSalesDetail.mockReturnValue({
    data: {
      data: { ...target, month_actual: null, month_target: null, month_ratio: null, gap: null },
    },
    error: null,
    retry: vi.fn(),
  });
  rerender(<SalesTargets month="2026-11" version="v3" onConflict={vi.fn()} />);
  expect(container.querySelector(".monthly-target")!.textContent).not.toMatch(/105.02%|0.00%|¥0/);
  expect(container.querySelector(".monthly-target")!.textContent).toContain("暂无数据");
  expect(container.querySelector(".target-summary")!.textContent).toContain("暂无数据");
  expect(container.querySelector(".monthly-target [role=progressbar]")).toBeNull();
  expect(container.querySelector(".annual-target")!.textContent).toContain("¥1,000");
});

it("整段无记录与真实零金额分别展示，月年独立判断", () => {
  useSalesDetail.mockReturnValue({
    data: {
      data: {
        ...target,
        month_actual: null,
        annual_actual: null,
        month_ratio: null,
        annual_ratio: null,
        gap: null,
      },
    },
    error: null,
    retry: vi.fn(),
  });
  const { container, rerender } = render(<SalesTargets month="2026-10" onConflict={vi.fn()} />);
  for (const card of container.querySelectorAll(".target-card")) {
    expect(card.textContent).toContain("暂无数据");
    expect(card.textContent).not.toMatch(/0\.00%|¥0(?:\D|$)/);
  }
  expect(screen.queryByRole("progressbar")).toBeNull();
  useSalesDetail.mockReturnValue({
    data: { data: { ...target, month_actual: "0.00", month_ratio: "0", gap: "25000" } },
    error: null,
    retry: vi.fn(),
  });
  rerender(<SalesTargets month="2026-10" onConflict={vi.fn()} />);
  const month = container.querySelector(".monthly-target")!;
  expect(month.textContent).toContain("¥0");
  expect(month.textContent).toContain("0.00%");
  expect(month.textContent).not.toContain("暂无数据");
  expect(
    within(month as HTMLElement)
      .getByRole("progressbar")
      .getAttribute("aria-valuenow"),
  ).toBe("0");
  expect(container.querySelector(".target-summary")!.textContent).toContain("¥25,000");
});

it("目标缺失不冒充无销售记录，加载和请求失败也不冒充空数据", () => {
  useSalesDetail.mockReturnValue({
    data: {
      data: {
        ...target,
        month_target: null,
        month_ratio: null,
        gap: null,
        state: "source_unavailable",
      },
    },
    error: null,
    retry: vi.fn(),
  });
  const { container, rerender } = render(<SalesTargets month="2026-10" onConflict={vi.fn()} />);
  expect(container.querySelector(".monthly-target")!.textContent).toContain("¥100");
  expect(container.textContent).toContain("暂未取得本月目标");
  expect(container.querySelector(".target-summary > .ant-card-body > strong")!.textContent).toBe(
    "—",
  );
  expect(container.querySelector(".monthly-target [role=progressbar]")).toBeNull();
  useSalesDetail.mockReturnValue({ data: null, error: null, retry: vi.fn() });
  rerender(<SalesTargets month="2026-11" onConflict={vi.fn()} />);
  expect(container.textContent).toContain("正在读取目标进度");
  expect(container.textContent).not.toContain("暂无数据");
  expect(container.querySelector("details")).toBeNull();
  useSalesDetail.mockReturnValue({ data: null, error: "读取失败", retry: vi.fn() });
  rerender(<SalesTargets month="2026-11" onConflict={vi.fn()} />);
  expect(screen.getByText("读取失败")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重试目标数据" })).toBeTruthy();
  expect(container.textContent).not.toContain("暂无数据");
});
