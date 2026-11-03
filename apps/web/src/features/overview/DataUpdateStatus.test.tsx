// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { DataUpdateStatus } from "./DataUpdateStatus";
import type { DomainState } from "./data";
import { updateRows, updateState } from "./updateStatus";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: () => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
    }),
  });
});
afterEach(cleanup);
const state = (): DomainState => ({
  id: "sales",
  data: undefined,
  loading: false,
  refreshing: false,
  error: null,
  refresh: vi.fn(),
  meta: {
    id: "sales",
    version: "v",
    date: "2026-11-01",
    completeThrough: null,
    success: null,
    attempt: null,
    attemptStatus: "succeeded",
    status: "observed",
    notes: [],
    raw: null,
    updates: [
      {
        id: "shop_regions",
        name: "店铺后台地区交易",
        date: "2026-10-31",
        success: "2026-11-02T11:02:18+08:00",
        sourceStatus: "observed",
        attemptStatus: "succeeded",
      },
    ],
  },
});

it("逐来源显示真实日期，成功同步不冒充上游最新；仅本地刷新可执行", () => {
  const s = { ...state(), refreshStatus: vi.fn() };
  const { rerender } = render(<DataUpdateStatus states={[s]} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("2026-10-31")).toBeTruthy();
  expect(dialog.queryByText("2026-11-01")).toBeNull();
  expect(dialog.getByText("上游待检查")).toBeTruthy();
  expect(dialog.getByText("尚未检查")).toBeTruthy();
  for (const name of ["检查上游更新", "全部同步"])
    expect((dialog.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(dialog.getByRole("button", { name: "刷新本地状态" }));
  expect(s.refreshStatus).toHaveBeenCalledTimes(1);
  expect(s.refresh).not.toHaveBeenCalled();
  rerender(
    <DataUpdateStatus
      states={[
        { ...s, meta: { ...s.meta!, updates: [{ ...s.meta!.updates![0], date: "2026-11-01" }] } },
      ]}
    />,
  );
  expect(dialog.getByText("2026-11-01")).toBeTruthy();
  expect(dialog.queryByText("2026-10-31")).toBeNull();
  fireEvent.click(dialog.getByRole("tab", { name: "评分与分析" }));
  expect(dialog.getByText("评分与分析更新状态待接入")).toBeTruthy();
  expect((dialog.getByRole("button", { name: "重新计算" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
});

it("空接口和查询失败不生成来源条目或正常状态", () => {
  const s = { ...state(), meta: undefined, error: "failed" };
  render(<DataUpdateStatus states={[s]} />);
  fireEvent.click(screen.getByRole("button", { name: "数据更新状态" }));
  expect(screen.getByText("暂无来源状态")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("状态未能读取");
  expect(screen.queryByText("正常")).toBeNull();
  const row = state().meta!.updates![0];
  expect(updateState({ ...row, attemptStatus: "failed" }).label).toBe("最近同步失败");
  expect(updateState({ ...row, queryError: "failed" }).label).toBe("状态读取失败");
  expect(
    updateRows([{ ...state(), error: "经营查询失败", statusError: null }])[0].queryError,
  ).toBeNull();
  expect(updateRows([{ ...state(), statusError: "元信息读取失败" }])[0].queryError).toBe(
    "元信息读取失败",
  );
});
