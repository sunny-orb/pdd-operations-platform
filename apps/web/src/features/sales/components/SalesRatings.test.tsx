// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SalesRatings } from "./SalesRatings";
import { syntheticAllShops, testFilters, testMeta } from "../testFixtures";
import type { FiltersResponse } from "../api/contracts";

const source = {
  state: "observed",
  observed_from: "2026-10-30",
  observed_through: "2026-11-01",
  source_updated_text: null,
  last_success_at: "2026-11-02T11:01:54+08:00",
  last_attempt_at: "2026-11-02T11:01:49+08:00",
  last_attempt_status: "succeeded",
};
function filters(start = source.observed_from, end = source.observed_through): FiltersResponse {
  return {
    ...testFilters,
    data: {
      ...testFilters.data,
      shops: syntheticAllShops,
      capabilities: { ratings: true },
      sources: { shop_ratings: { ...source, observed_from: start, observed_through: end } },
    },
  };
}
const latest = syntheticAllShops.map((s, i) => ({
  shop_id: s.shop_id,
  date: "2026-11-01",
  state: ["observed", "observed", "field_null", "no_records", "invalid_score", "legacy_basis"][i],
  raw_score: ["0.9525", "0", null, null, "-0.2", "4.8"][i],
  score: ["95.2500", "0", null, null, null, null][i],
  source_created_time_raw: i === 3 ? null : "2026-11-02 06:05:41",
}));
const onConflict = () => {};
let calls: URL[];
beforeEach(() => {
  calls = [];
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (path: string) => {
      const url = new URL(path, "http://localhost");
      calls.push(url);
      return {
        ok: true,
        json: async () => ({
          data: { latest_date: "2026-11-01", latest, points: latest, source },
          meta: { ...testMeta, data_version: url.searchParams.get("data_version") },
        }),
      };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("卡片保留真实零，区分空值、缺行和异常；来源折叠，选店只显示当前店", async () => {
  const f = filters();
  const { container, rerender } = render(
    <SalesRatings filters={f} shop="" onConflict={onConflict} />,
  );
  await screen.findByText("95.25 分");
  const cards = container.querySelectorAll(".store-rating-card");
  expect(cards).toHaveLength(6);
  expect(cards[0].textContent).toContain("评分日期：2026-11-01");
  expect(cards[0].textContent).not.toContain("百分制");
  expect(cards[1].textContent).toContain("0.00 分");
  expect(cards[2].textContent).toContain("当日评分为空");
  expect(cards[3].textContent).toContain("当日暂无评分记录");
  expect(cards[4].textContent).toContain("当日评分异常");
  expect(cards[4].textContent).not.toContain("-0.20");
  expect(cards[5].textContent).toContain("4.80 分（旧口径）");
  const details = screen.getByText("查看评分说明").closest("details")!;
  expect(details.open).toBe(false);
  expect(within(details).getByText(/最近检查时间/).textContent).toContain("11:01:49");
  fireEvent.click(screen.getByText("查看评分说明"));
  expect(details.open).toBe(true);
  rerender(<SalesRatings filters={f} shop="spifs" onConflict={onConflict} />);
  expect(container.querySelectorAll(".store-rating-card")).toHaveLength(1);
  expect(container.querySelector(".store-rating-card")?.textContent).toContain("服饰店");
});

it.each([
  ["2026-10-30", "2026-11-01", "2026-10-30"],
  ["2026-02-01", "2026-11-01", "2026-10-03"],
  ["2027-12-01", "2028-01-02", "2027-12-04"],
  ["2028-02-28", "2028-03-01", "2028-02-28"],
])("默认趋势遵守实际边界 %s 至 %s", async (start, end, expected) => {
  render(<SalesRatings filters={filters(start, end)} shop="" onConflict={onConflict} />);
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  expect(calls[0].searchParams.get("date_from")).toBe(expected);
  expect(calls[0].searchParams.get("date_to")).toBe(end);
});

it("选择日期后卡片仍是最新日，来源范围缩小时重新限制查询边界", async () => {
  const { rerender } = render(<SalesRatings filters={filters()} shop="" onConflict={onConflict} />);
  await screen.findByText("95.25 分");
  const end = screen.getByLabelText("评分趋势结束日期");
  fireEvent.change(end, { target: { value: "2026-10-31" } });
  fireEvent.keyDown(end, { key: "Enter", code: "Enter" });
  fireEvent.blur(end);
  await waitFor(() => expect(calls.at(-1)?.searchParams.get("date_to")).toBe("2026-10-31"));
  await screen.findByText("95.25 分");
  expect(screen.getByText(/最新评分日期/).textContent).toContain("2026-11-01");
  rerender(
    <SalesRatings filters={filters("2026-11-01", "2026-11-01")} shop="" onConflict={onConflict} />,
  );
  await waitFor(() => expect(calls.at(-1)?.searchParams.get("date_from")).toBe("2026-11-01"));
  expect(calls.at(-1)?.searchParams.get("date_to")).toBe("2026-11-01");
});

it("未取得响应时不误报缺行或字段空，空来源不请求无效日期", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  const { container, rerender } = render(
    <SalesRatings filters={filters()} shop="" onConflict={onConflict} />,
  );
  expect(container.querySelector(".store-rating-card")?.textContent).toContain("评分读取中");
  expect(container.textContent).not.toContain("缺记录或未接入");
  const empty = filters();
  empty.data.sources!.shop_ratings = { ...source, observed_from: null, observed_through: null };
  vi.mocked(fetch).mockClear();
  rerender(<SalesRatings filters={empty} shop="" onConflict={onConflict} />);
  expect(container.querySelector(".store-rating-card")?.textContent).toContain("暂无评分数据");
  expect(fetch).not.toHaveBeenCalled();
});

it("请求失败与数据缺失分开，检查失败时保留已发布评分并常显简短提示", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
  const f = filters();
  f.data.sources!.shop_ratings = { ...source, last_attempt_status: "failed" };
  const { container } = render(<SalesRatings filters={f} shop="spigf" onConflict={onConflict} />);
  await screen.findByText("评分暂未取得");
  expect(container.querySelector(".store-rating-card")?.textContent).not.toMatch(
    /当日评分为空|暂无评分记录/,
  );
  fireEvent.click(screen.getByRole("button", { name: "重试评分" }));
  await screen.findByText("95.25 分");
  expect(screen.getByText(/评分最近检查未成功/).closest("details")).toBeNull();
  expect(screen.getByText("查看评分说明").closest("details")!.open).toBe(false);
});

it("趋势按本区选店更新列和缺失原因，全取消有提示，旧口径保留原值不伪装成百分制", async () => {
  const { container } = render(
    <SalesRatings filters={filters()} shop="spigf" onConflict={onConflict} />,
  );
  await screen.findByText("95.25 分");
  const panel = within(container.querySelector(".rating-trend-panel")! as HTMLElement);
  const details = panel.getByText("查看评分趋势明细").closest("details")!;
  expect(details.open).toBe(false);
  fireEvent.click(panel.getByText("查看评分趋势明细"));
  for (const name of ["雨具店", "箱包店", "童装童鞋店"])
    fireEvent.click(panel.getByRole("checkbox", { name }));
  const row = panel.getByRole("row", { name: /2026-11-01/ });
  expect(row.textContent).toContain("95.25");
  expect(row.textContent).toContain("0.00");
  expect(row.textContent).toContain("评分为空");
  expect(row.textContent).toContain("暂无记录");
  expect(row.textContent).toContain("评分异常");
  expect(row.textContent).toContain("4.80（旧口径）");
  for (const checkbox of panel.getAllByRole("checkbox")) fireEvent.click(checkbox);
  expect(panel.getByRole("status").textContent).toContain("请选择店铺");
  expect(panel.queryByRole("table")).toBeNull();
  fireEvent.click(panel.getByRole("checkbox", { name: "童装童鞋店" }));
  expect(panel.getByRole("status").textContent).toContain("暂无可绘制的评分");
  expect(panel.getByText("查看评分趋势明细").closest("details")!.open).toBe(true);
  expect(panel.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "日期",
    "童装童鞋店",
  ]);
  expect(panel.getByRole("row", { name: /2026-11-01/ }).textContent).toContain("4.80（旧口径）");
});

it("趋势日期改变后等待当前结果，加载期间不显示旧分数或误报缺记录", async () => {
  const { container } = render(
    <SalesRatings filters={filters()} shop="" onConflict={onConflict} />,
  );
  await screen.findByText("95.25 分");
  const panel = within(container.querySelector(".rating-trend-panel")! as HTMLElement);
  fireEvent.click(panel.getByText("查看评分趋势明细"));
  let resolve!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const end = screen.getByLabelText("评分趋势结束日期");
  fireEvent.change(end, { target: { value: "2026-10-31" } });
  fireEvent.keyDown(end, { key: "Enter", code: "Enter" });
  fireEvent.blur(end);
  await waitFor(() => expect(panel.getByRole("status").textContent).toContain("正在加载评分趋势"));
  expect(panel.queryByRole("table")).toBeNull();
  expect(panel.queryByText(/暂无记录/)).toBeNull();
  resolve({
    ok: true,
    json: async () => ({
      data: {
        latest_date: "2026-11-01",
        latest,
        points: [{ ...latest[0], date: "2026-10-31", score: "86.77" }],
        source,
      },
      meta: testMeta,
    }),
  } as Response);
  const row = await panel.findByRole("row", { name: /2026-10-31.*86.77/ });
  expect(row.textContent).not.toContain("95.25");
  expect(panel.queryByRole("row", { name: /2026-11-01/ })).toBeNull();
  expect(panel.getByText("查看评分趋势明细").closest("details")!.open).toBe(true);
});

it("多日评分和单个真实零都有曲线圆点，空评分不画零点，选店不换色", async () => {
  const rect = {
    width: 900,
    height: 310,
    top: 0,
    left: 0,
    right: 900,
    bottom: 310,
    x: 0,
    y: 0,
    toJSON() {},
  };
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback(
          [{ target, contentRect: rect } as ResizeObserverEntry],
          this as unknown as ResizeObserver,
        );
      }
      unobserve() {}
      disconnect() {}
    },
  );
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      data: {
        latest_date: "2026-11-01",
        latest,
        points: [...latest, { ...latest[0], date: "2026-10-30", score: "94.10" }],
        source,
      },
      meta: testMeta,
    }),
  } as Response);
  const { container } = render(
    <SalesRatings filters={filters()} shop="" onConflict={onConflict} />,
  );
  await screen.findByText("95.25 分");
  await waitFor(() => expect(container.querySelectorAll(".recharts-line-dot")).toHaveLength(3));
  expect(screen.getByText("评分（分）")).toBeTruthy();
  const chart = screen.getByRole("application");
  fireEvent.keyDown(chart, { key: "ArrowRight" });
  fireEvent.keyDown(chart, { key: "ArrowRight" });
  fireEvent.keyDown(chart, { key: "ArrowRight" });
  await waitFor(() =>
    expect(container.querySelector(".rating-trend-tooltip")?.textContent).toContain("2026-11-01"),
  );
  expect(container.querySelector(".rating-trend-tooltip")?.textContent).toContain("95.25");
  expect(container.querySelector(".rating-trend-tooltip")?.textContent).toContain("0.00");
  expect(container.querySelector(".rating-trend-tooltip")?.textContent).toContain("评分为空");
  const zeroDot = container.querySelectorAll(".recharts-line-dot")[2];
  const color = zeroDot.getAttribute("stroke");
  fireEvent.click(screen.getByRole("checkbox", { name: "官方店" }));
  await waitFor(() => expect(container.querySelectorAll(".recharts-line-dot")).toHaveLength(1));
  expect(container.querySelector(".recharts-line-dot")!.getAttribute("stroke")).toBe(color);
});
