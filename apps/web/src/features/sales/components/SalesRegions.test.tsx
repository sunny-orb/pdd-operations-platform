// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SalesRegions } from "./SalesRegions";
import { testFilters, testMeta, syntheticAllShops } from "../testFixtures";

const source = {
  state: "observed",
  observed_from: "2026-10-01",
  observed_through: "2026-10-31",
  source_updated_text: null,
  last_success_at: null,
  last_attempt_status: "succeeded",
};
const filters = {
  ...testFilters,
  data: {
    ...testFilters.data,
    shops: syntheticAllShops,
    capabilities: { regions: true },
    sources: { shop_regions: source },
  },
};
const onConflict = () => {};
function response(kind = "gaps") {
  return {
    meta: testMeta,
    data: {
      items:
        kind === "empty"
          ? []
          : [
              {
                region: "浙江",
                transaction_amount: "0.00",
                transaction_orders: "0",
                transaction_buyers: "0",
                share: null,
                aov: null,
                null_counts: {},
              },
            ],
      totals: {
        transaction_amount: kind === "empty" ? null : "0.00",
        transaction_orders: kind === "empty" ? null : "0",
        transaction_buyers: kind === "empty" ? null : "0",
      },
      region_count: kind === "empty" ? 0 : 1,
      missing_shop_days: kind === "gaps" ? ["spixb:2026-10-31"] : [],
      null_counts: kind === "gaps" ? { transaction_amount: 2 } : {},
      field_gaps:
        kind === "gaps"
          ? [{ shop_id: "spigf", date: "2026-10-31", fields: { transaction_amount: 2 } }]
          : [],
      source,
    },
  };
}
beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const style = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((e) => style(e));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("缺记录与金额为空列出各自店日，切换结果后清掉旧提示，真实零不报异常", async () => {
  let data = response();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => Promise.resolve({ ok: true, json: async () => data })),
  );
  const view = render(<SalesRegions filters={filters} onConflict={onConflict} />);
  await waitFor(() => expect(screen.getByLabelText("地区交易数据提示")).toBeTruthy());
  const note = screen.getByText("查看地区交易明细与口径").closest("details")!;
  expect(note.open).toBe(false);
  fireEvent.click(screen.getByText("查看地区交易明细与口径"));
  expect(note.textContent).toContain("箱包店");
  expect(note.textContent).toContain("暂无记录：2026-10-31");
  expect(note.textContent).toContain("官方店");
  expect(note.textContent).toContain("成交金额未提供：2条地区记录");
  data = response("zero");
  view.rerender(
    <SalesRegions
      filters={{ ...filters, meta: { ...testMeta, data_version: "zero-v2" } }}
      onConflict={onConflict}
    />,
  );
  data.meta = { ...testMeta, data_version: "zero-v2" };
  await waitFor(() =>
    expect(document.querySelector("#regions .region-overview strong")?.textContent).toBe("¥0"),
  );
  expect(screen.queryByLabelText("地区交易数据提示")).toBeNull();
  expect(screen.queryByText(/暂无记录：2026-10-31/)).toBeNull();
});

it("整期无记录不当成零，日期恢复和店铺恢复互不改动", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) =>
      Promise.resolve({
        ok: true,
        json: async () => {
          const u = new URL(path, "http://localhost");
          return {
            ...response(u.searchParams.has("shop_id") ? "empty" : "zero"),
            meta: { ...testMeta, data_version: u.searchParams.get("data_version")! },
          };
        },
      }),
    ),
  );
  render(<SalesRegions filters={filters} onConflict={onConflict} />);
  const region = within(document.querySelector<HTMLElement>("#regions")!);
  const start = region.getByRole("textbox", { name: "地区交易开始日期" });
  fireEvent.change(start, { target: { value: "2026/10/30" } });
  fireEvent.keyDown(start, { key: "Enter", code: "Enter" });
  await waitFor(() => expect(region.getByRole("button", { name: "恢复最新日" })).toBeTruthy());
  fireEvent.mouseDown(region.getByRole("combobox", { name: "地区交易店铺筛选" }));
  fireEvent.click(screen.getByText("箱包店", { selector: ".ant-select-item-option-content" }));
  await waitFor(() => expect(region.getByText("暂无地区交易记录")).toBeTruthy());
  expect(document.querySelector("#regions .region-overview strong")?.textContent).toBe("—");
  fireEvent.click(region.getByRole("button", { name: "恢复最新日" }));
  expect(region.getByRole("button", { name: "显示全部店铺" })).toBeTruthy();
  expect(document.querySelector("#regions .region-overview small")?.textContent).toContain(
    "箱包店",
  );
  expect((start as HTMLInputElement).value).toBe("2026/10/31");
  fireEvent.change(start, { target: { value: "2026/10/30" } });
  fireEvent.keyDown(start, { key: "Enter", code: "Enter" });
  fireEvent.click(region.getByRole("button", { name: "显示全部店铺" }));
  expect((start as HTMLInputElement).value).toBe("2026/10/30");
  await waitFor(() =>
    expect(document.querySelector("#regions .region-overview strong")?.textContent).toBe("¥0"),
  );
});
