// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { testFilters, testMetrics } from "../testFixtures";
import { SalesNotes } from "./SalesNotes";

const row = {
  shop_id: "spigf",
  shop_name: "官方店原名",
  date: "2026-10-26",
  source: "飞书历史补齐",
  source_url: "https://example.com/history",
  source_record_id: "one",
  spend: "1234.56",
  revenue: "6789.01",
  status: "supplement" as const,
};
const data = { ...testMetrics, promotion_supplements: [row] };
afterEach(cleanup);

it("默认收起，只保留三句口径和本期生效清单，不显示技术来源及范围外历史", async () => {
  const { container } = render(
    <SalesNotes
      shopId=""
      shops={testFilters.data.shops}
      data={{
        ...data,
        promotion_supplements: [
          row,
          { ...row, date: "2026-10-25", status: "warehouse" },
          { ...row, date: "2026-02-21", status: "outside_range" },
        ],
      }}
    />,
  );
  expect(container.querySelector("details")!.open).toBe(false);
  await userEvent.click(screen.getByText("查看统计口径与历史补充"));
  expect(container.querySelector("details")!.open).toBe(true);
  expect(container.querySelectorAll(".sales-notes-definitions li")).toHaveLength(3);
  expect(screen.getByText("访客、买家按每天的数据累计。")).toBeTruthy();
  expect(screen.getByText("多店、多日的数据先汇总，再计算比率。")).toBeTruthy();
  expect(
    screen.getByText("推广成交金额是平台归因到推广渠道的成交金额，按支付日统计。"),
  ).toBeTruthy();
  const table = screen.getByRole("table", { name: "本期实际使用的历史补充" });
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(table.textContent).toContain("官方店2026-10-26¥1,234.56¥6,789.01已核实补充");
  expect(container.textContent).not.toMatch(/2026-10-25|2026-02-21|当前范围外|数仓|最近检查|ROI/);
});

it("选店、日期边界和数仓替换后更新清单；无生效记录显示指定短句", async () => {
  const other = { ...row, shop_id: "spihw", date: "2026-10-27" };
  const { container, rerender } = render(
    <SalesNotes shopId="" data={{ ...data, promotion_supplements: [row, other] }} />,
  );
  await userEvent.click(screen.getByText("查看统计口径与历史补充"));
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3);
  rerender(
    <SalesNotes
      shopId="spihw"
      shops={testFilters.data.shops}
      data={{
        ...data,
        coverage: { ...data.coverage, window_start: "2026-10-27", window_end: "2026-10-27" },
        promotion_supplements: [row, other],
      }}
    />,
  );
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  expect(screen.getByRole("table").textContent).toContain("户外店");
  expect(container.textContent).not.toContain("2026-10-26");
  rerender(
    <SalesNotes
      shopId="spihw"
      data={{ ...data, promotion_supplements: [{ ...other, status: "warehouse" }] }}
    />,
  );
  expect(screen.getByText("本期未使用历史补充记录")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

it("真实零保留，已确认未投放只来自确认依据，不能仅凭零金额推断", async () => {
  render(
    <SalesNotes
      shopId=""
      data={{
        ...data,
        promotion_supplements: [
          { ...row, spend: "0", revenue: "0" },
          { ...row, date: "2026-10-27", spend: "0", revenue: "0", source: "原版停投确认记录" },
        ],
      }}
    />,
  );
  await userEvent.click(screen.getByText("查看统计口径与历史补充"));
  const rows = within(screen.getByRole("table")).getAllByRole("row");
  expect(rows[1].textContent).toContain("¥0.00¥0.00已核实补充");
  expect(rows[2].textContent).toContain("¥0.00¥0.00已确认未投放");
});

it("加载或旧响应缺清单时不误称本期未使用，恢复后清除旧记录", async () => {
  const { container, rerender } = render(<SalesNotes shopId="" data={data} />);
  await userEvent.click(screen.getByText("查看统计口径与历史补充"));
  rerender(<SalesNotes shopId="" />);
  expect(screen.getByText("暂未取得本期补充记录")).toBeTruthy();
  expect(container.textContent).not.toMatch(/1234|2026-10-26|本期未使用/);
  rerender(<SalesNotes shopId="" data={{ ...data, promotion_supplements: [] }} />);
  expect(screen.getByText("本期未使用历史补充记录")).toBeTruthy();
});
