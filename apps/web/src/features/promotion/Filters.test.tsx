// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider } from "antd";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Filters } from "./Filters";
import type { PromotionState } from "./data";
import { readScope } from "./state";

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Harness({
  mode = "product",
  level = "",
  labelRetry,
}: {
  mode?: "store" | "product";
  level?: string;
  labelRetry?: () => void;
}) {
  const [scope, setScope] = useState(
    readScope(new URLSearchParams({ product_level: level }), "product", "2026-10-03", "2026-11-01"),
  );
  const [needle, setNeedle] = useState("");
  const [loading, setLoading] = useState(false);
  const state = {
    mode,
    scope,
    needle,
    setNeedle,
    selectedOptions: labelRetry
      ? { error: new Error("unavailable"), refetch: labelRetry }
      : undefined,
    selectedProducts: [
      { value: "p1", label: "测试商品", level: "多等级", level_details: ["A级", "B级"] },
    ],
    shopOptions: [{ value: "shop1", label: "测试店铺" }],
    change: (patch: Partial<typeof scope>) => setScope((previous) => ({ ...previous, ...patch })),
    meta: { data: { data: { date_from: "2026-01-01", date_to: "2026-11-01" } } },
    options: {
      isFetching: false,
      data: loading
        ? undefined
        : {
            data: {
              shops: [{ value: "shop1", label: "测试店铺" }],
              categories: ["童装"],
              levels: ["A"],
              operators: ["小王"],
              products: [
                { value: "p1", label: "测试商品", level: "多等级", level_details: ["A级", "B级"] },
              ],
              selected_products: [],
              total: 1,
            },
          },
    },
  } as unknown as PromotionState;
  return (
    <>
      <ConfigProvider theme={{ token: { motion: false } }}>
        <Filters state={state} />
      </ConfigProvider>
      <div>筛选框外空白</div>
      <button onClick={() => setLoading(true)}>新日期目录加载</button>
      <output aria-label="已选条件">{JSON.stringify(scope)}</output>
    </>
  );
}

it("商品候选直接说明多等级包含的原始等级", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByLabelText("输入商品 ID 或商品名称 筛选"));
  expect(screen.getByText("多等级（含A级、B级）")).toBeTruthy();
});

it("旧链接的精确混合等级在选框中可读，选择其他字段不扩大原等级", async () => {
  render(<Harness level="A级/B级混合" />);
  expect(screen.getByLabelText("产品等级").closest(".ant-select")?.textContent).toContain(
    "A级/B级混合",
  );
  await userEvent.click(screen.getByLabelText("店铺筛选"));
  await userEvent.click(
    screen.getByText("测试店铺", { selector: ".ant-select-item-option-content" }),
  );
  expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).level).toBe("A级/B级混合");
});

it("已选名称补取失败单独显示可重试状态，不清空当前条件", async () => {
  const retry = vi.fn();
  render(<Harness level="A级/B级混合" labelRetry={retry} />);
  expect(screen.getByRole("alert").textContent).toContain("已选商品名称暂时无法加载");
  await userEvent.click(screen.getByRole("button", { name: "重试商品名称" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).level).toBe("A级/B级混合");
});

it("更换日期加载选项时，已选店铺标签保持可见", async () => {
  render(<Harness />);
  await userEvent.click(screen.getByLabelText("店铺筛选"));
  await userEvent.click(
    screen.getByText("测试店铺", { selector: ".ant-select-item-option-content" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "新日期目录加载" }));
  expect(screen.getByLabelText("店铺筛选").closest(".ant-select")?.textContent).toContain(
    "测试店铺",
  );
});

it.each(["开始日期", "结束日期", "店铺筛选", "品类筛选", "产品等级", "运营筛选"])(
  "%s再次点击和点击外部均收起、失焦，保留条件",
  async (label) => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText(label);
    const before = screen.getByLabelText("已选条件").textContent;
    const isOpen = () =>
      label.includes("日期")
        ? Boolean(document.querySelector(".ant-picker-dropdown:not(.ant-picker-dropdown-hidden)"))
        : input.getAttribute("aria-expanded") === "true";
    await user.click(input);
    await waitFor(() => expect(isOpen()).toBe(true));
    await user.click(input);
    await waitFor(() => expect(isOpen()).toBe(false));
    expect(document.activeElement).not.toBe(input);
    await user.click(input);
    await waitFor(() => expect(isOpen()).toBe(true));
    await user.click(screen.getByText("筛选框外空白"));
    await waitFor(() => expect(isOpen()).toBe(false));
    expect(document.activeElement).not.toBe(input);
    expect(screen.getByLabelText("已选条件").textContent).toBe(before);
  },
);

it("搜索和选择仍有效，切换筛选只展开当前框", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const shop = screen.getByLabelText("店铺筛选");
  await user.click(shop);
  await user.keyboard("测试");
  await user.click(screen.getByText("测试店铺", { selector: ".ant-select-item-option-content" }));
  await waitFor(() =>
    expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).shop).toBe("shop1"),
  );
  await user.click(shop);
  const category = screen.getByLabelText("品类筛选");
  await user.click(category);
  await waitFor(() => expect(shop.getAttribute("aria-expanded")).toBe("false"));
  expect(category.getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByText("童装", { selector: ".ant-select-item-option-content" }));
  expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).category).toBe("童装");
});

it("日期弹层内翻月和选日不被拦截，起止日期独立", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByLabelText("开始日期"));
  await user.click(screen.getByRole("button", { name: "Previous month" }));
  const day = document.querySelector('[title="2026-09-15"]')!;
  await user.click(day);
  await waitFor(() =>
    expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).from).toBe("2026-09-15"),
  );
  expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).to).toBe("2026-11-01");
});

it("键盘Escape收起后保留焦点，方向键可重新展开", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const level = screen.getByLabelText("产品等级");
  await user.click(level);
  fireEvent.keyDown(level, { key: "Escape", code: "Escape", keyCode: 27, which: 27 });
  await waitFor(() => expect(level.getAttribute("aria-expanded")).toBe("false"));
  expect(document.activeElement).toBe(level);
  fireEvent.keyDown(level, { key: "ArrowDown", code: "ArrowDown", keyCode: 40, which: 40 });
  expect(level.getAttribute("aria-expanded")).toBe("true");
  fireEvent.keyDown(level, { key: "ArrowDown", code: "ArrowDown", keyCode: 40, which: 40 });
  fireEvent.keyDown(level, { key: "Enter", code: "Enter", keyCode: 13, which: 13 });
  await waitFor(() =>
    expect(JSON.parse(screen.getByLabelText("已选条件").textContent!).level).toBe("A"),
  );
});

it("店铺总览复用筛选的开合正常，收起渠道不改变筛选", async () => {
  const user = userEvent.setup();
  render(<Harness mode="store" />);
  const channel = screen.getByLabelText("推广渠道");
  const before = screen.getByLabelText("已选条件").textContent;
  await user.click(channel);
  expect(channel.getAttribute("aria-expanded")).toBe("true");
  await user.click(channel);
  await waitFor(() => expect(channel.getAttribute("aria-expanded")).toBe("false"));
  expect(document.activeElement).not.toBe(channel);
  expect(screen.getByLabelText("已选条件").textContent).toBe(before);
});
