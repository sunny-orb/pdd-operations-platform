// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SalesPage } from "./SalesPage";
import { syntheticResponse } from "./testFixtures";

beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => computed(element));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("接口失败仍保留原型区块、八卡、四折叠及地区与十列表头", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  expect(screen.getByRole("heading", { name: "店铺销售看板" })).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重新读取本地数据" })).toBeTruthy(),
  );
  for (const title of [
    "销售金额",
    "店铺访客",
    "成交转化率",
    "客单价",
    "退款率",
    "推广花费",
    "点击率",
    "ROI",
  ]) {
    expect(within(screen.getByTestId("sales-kpis")).getByText(title)).toBeTruthy();
  }
  expect(document.querySelectorAll("details").length).toBe(4);
  expect(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getAllByRole("columnheader"),
  ).toHaveLength(10);
  expect(
    within(screen.getByRole("table", { name: "店铺后台地区数据" })).getAllByRole("columnheader"),
  ).toHaveLength(6);
  expect(
    within(screen.getByRole("table", { name: "聚水潭产品地区数据" })).getAllByRole("columnheader"),
  ).toHaveLength(7);
  expect(screen.getByText("本月 GMV 目标进度")).toBeTruthy();
  expect(screen.queryByText(/开发样例/)).toBeNull();
});

it("单店切换与再次点击恢复不会改变全店贡献和底表分母", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((path: string) =>
        Promise.resolve({ ok: true, json: async () => syntheticResponse(path) }),
      ),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const kpis = screen.getByTestId("sales-kpis");
  await waitFor(() => expect(within(kpis).getByText("¥31")).toBeTruthy());
  const table = screen.getByRole("table", { name: "店铺完整数据" });
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.click(within(table).getByRole("button", { name: "户外店" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "户外店经营表现" })).toBeTruthy());
  await waitFor(() => expect(within(kpis).getByText("¥0")).toBeTruthy());
  expect(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByText("100.0%"),
  ).toBeTruthy();
  fireEvent.click(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByRole("button", {
      name: "户外店",
    }),
  );
  await waitFor(() => expect(within(kpis).getByText("¥31")).toBeTruthy());
});

it("连续409后停止自动重试", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      calls++;
      return Promise.resolve(
        path.includes("/filters")
          ? { ok: true, json: async () => syntheticResponse(path) }
          : { ok: false, status: 409 },
      );
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重新读取本地数据" })).toBeTruthy(),
  );
  expect(calls).toBe(6);
  expect(screen.getByTestId("sales-kpis").textContent).not.toContain("¥31");
});

it("来源尝试失败后展示失败状态和实际尝试时间", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      const response = syntheticResponse(path);
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...response,
          meta: {
            ...response.meta,
            last_attempt_status: "failed",
            last_attempt_at: "2026-10-28T01:02:03Z",
          },
        }),
      });
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/最近尝试：失败/)).toBeTruthy());
  expect(screen.getByText(/最近尝试：失败/).textContent).toContain(
    new Date("2026-10-28T01:02:03Z").toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour12: false,
    }),
  );
});

it("切换店铺后忽略已取消请求的晚到响应", async () => {
  let release = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((path: string) => {
      if (path.includes("shop_id=spihw"))
        return new Promise((resolve) => {
          release = () => resolve({ ok: true, json: async () => syntheticResponse(path) });
        });
      return Promise.resolve({ ok: true, json: async () => syntheticResponse(path) });
    }),
  );
  render(
    <MemoryRouter>
      <SalesPage />
    </MemoryRouter>,
  );
  const { fireEvent } = await import("@testing-library/react");
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
  fireEvent.click(
    within(screen.getByRole("table", { name: "店铺完整数据" })).getByRole("button", {
      name: "户外店",
    }),
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "显示全部店铺" })).toBeTruthy());
  expect(within(screen.getByTestId("sales-kpis")).queryByText("¥31")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "显示全部店铺" }));
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
  release();
  await waitFor(() =>
    expect(within(screen.getByTestId("sales-kpis")).getByText("¥31")).toBeTruthy(),
  );
});
