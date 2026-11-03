// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { HistoricalSupplements } from "./HistoricalSupplements";
afterEach(cleanup);
it("区分生效、被数仓替换与范围外，保留原来源链接", () => {
  render(
    <HistoricalSupplements
      rows={[
        {
          shop_id: "a",
          shop_name: "服饰店",
          date: "2026-08-23",
          source: "飞书历史补齐",
          source_url: "https://kocotree.feishu.cn/base/example?table=test",
          source_record_id: "r1",
          spend: "1.25",
          revenue: "5",
          status: "supplement",
        },
        {
          shop_id: "b",
          shop_name: "户外店",
          date: "2026-02-21",
          source: "飞书历史补齐",
          source_url: null,
          source_record_id: "r2",
          spend: "0",
          revenue: "0",
          status: "warehouse",
        },
        {
          shop_id: "c",
          shop_name: "箱包店",
          date: "2026-08-15",
          source: "原版停投确认记录",
          source_url: null,
          source_record_id: null,
          spend: "0",
          revenue: "0",
          status: "outside_range",
        },
      ]}
    />,
  );
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByRole("link", { name: "飞书历史补齐" }).getAttribute("href")).toContain(
    "table=test",
  );
  expect(screen.getByText(/服饰店.*补齐生效/)).toBeTruthy();
  expect(screen.getByText(/户外店.*数仓已替换/)).toBeTruthy();
  expect(screen.getByText(/箱包店.*当前范围外/)).toBeTruthy();
});
it("未发布历史版本时不宣称补齐成功", () => {
  render(<HistoricalSupplements />);
  expect(screen.getByText(/历史补齐尚未接入/)).toBeTruthy();
  expect(screen.queryAllByRole("listitem")).toHaveLength(0);
});
