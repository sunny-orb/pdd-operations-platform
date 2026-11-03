// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Overview } from "./contracts";
import { Trend } from "./Trend";

afterEach(cleanup);

it("加载、无记录与投产比无法计算分别说明，不把有成交误称没有数据", () => {
  const data = fixture();
  for (const p of data.series) {
    p.metrics.roi = { value: null, status: "zero_denominator" } as never;
    p.previous = {};
  }
  const { rerender } = render(<Trend data={undefined} selected={["roi"]} />);
  expect(screen.getByText("正在读取趋势…")).toBeTruthy();
  rerender(<Trend data={data} selected={["roi"]} />);
  expect(screen.getByText("所选指标暂无可绘制数值")).toBeTruthy();
  expect(screen.queryByText("当前范围暂无推广数据")).toBeNull();
});

function fixture() {
  return {
    series: [1, 2, 3].map((n) => ({
      date: `2026-10-0${n}`,
      previous_date: `2025-10-0${n}`,
      missing: true,
      metrics: { revenue: { value: "123.45" }, spend: { value: "10.01" } },
      previous: { revenue: { value: "100.01" }, spend: { value: "0" } },
    })),
  } as unknown as Overview;
}

it("缺商品日不吞有效点，两实两虚和悬停原值都保留", () => {
  const data = fixture();
  const { container } = render(<Trend data={data} selected={["revenue", "spend"]} />);
  expect(container.querySelectorAll("path[data-series]")).toHaveLength(4);
  expect(container.querySelector('[data-series="revenue-now"]')?.getAttribute("d")).toContain("L");
  fireEvent.mouseEnter(screen.getByRole("button", { name: "查看2026-10-02推广数据" }));
  expect(container.querySelector("[data-cursor-line]")).not.toBeNull();
  expect(container.querySelectorAll("circle[data-active-point]")).toHaveLength(4);
  expect(screen.getByText("交易额 ¥123")).toBeTruthy();
  expect(screen.getByText("交易额 · 去年同期 ¥100")).toBeTruthy();
  fireEvent.mouseLeave(screen.getByRole("img"));
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
});

it("只在无值指标处断线，单点真实零可见，无同期不画虚线", () => {
  const data = fixture();
  data.series[1].metrics.revenue.value = null;
  for (const p of data.series) p.previous = {};
  const { container, rerender } = render(<Trend data={data} selected={["revenue", "spend"]} />);
  const line = container.querySelector('[data-series="revenue-now"]');
  expect(line?.getAttribute("d")?.match(/M/g)).toHaveLength(2);
  expect(line?.getAttribute("d")).not.toContain("L");
  expect(container.querySelectorAll('path[data-series$="prior"]')).toHaveLength(0);
  data.series = [
    {
      ...data.series[0],
      metrics: {
        ...data.series[0].metrics,
        revenue: { ...data.series[0].metrics.revenue, value: "0" },
      },
    },
  ];
  rerender(<Trend data={data} selected={["revenue"]} />);
  expect(container.querySelector("circle[data-isolated-point]")).not.toBeNull();
  expect(container.textContent).not.toContain("¥-");
});

it("空范围明确无数据，切换数据不会显示旧日期浮层", () => {
  const data = fixture();
  const { container, rerender } = render(<Trend data={data} selected={["revenue"]} />);
  fireEvent.focus(screen.getByRole("button", { name: "查看2026-10-02推广数据" }));
  expect(container.querySelector(".trend-tooltip")).not.toBeNull();
  rerender(<Trend data={{ ...data, series: [] }} selected={["revenue"]} />);
  expect(container.querySelector(".trend-tooltip")).toBeNull();
  expect(screen.getByText("当前范围暂无推广数据")).toBeTruthy();
});

it("日期保留首尾且不重叠，键盘能够逐日查看", () => {
  const data = fixture();
  data.series = Array.from({ length: 30 }, (_, i) => ({
    ...data.series[0],
    date: `2026-10-${String(i + 1).padStart(2, "0")}`,
  }));
  const { container } = render(<Trend data={data} selected={["revenue"]} />);
  const labels = [...container.querySelectorAll("text[data-date-label]")];
  expect(labels[0]?.textContent).toBe("10-01");
  expect(labels.at(-1)?.textContent).toBe("10-30");
  for (let i = 1; i < labels.length; i++)
    expect(
      Number(labels[i].getAttribute("x")) - Number(labels[i - 1].getAttribute("x")),
    ).toBeGreaterThanOrEqual(48);
  fireEvent.focus(screen.getByRole("button", { name: "查看2026-10-02推广数据" }));
  fireEvent.keyDown(screen.getByRole("button", { name: "查看2026-10-02推广数据" }), {
    key: "ArrowRight",
  });
  expect(screen.getByText("2026-10-03")).toBeTruthy();
});

it("缺项比率本期和去年同期沿线上名称展示，数值不隐藏", () => {
  const data = fixture();
  for (const point of data.series) {
    for (const metrics of [point.metrics, point.previous]) {
      metrics.cpc = { value: "2.5", status: "partial", type: "currency" } as never;
      metrics.ctr = { value: "0.02", status: "partial", type: "percent" } as never;
      metrics.cvr = { value: "0.5", status: "partial", type: "percent" } as never;
    }
  }
  render(<Trend data={data} selected={["cpc", "ctr", "cvr"]} />);
  fireEvent.click(screen.getByRole("button", { name: "查看2026-10-01推广数据" }));
  for (const text of [
    "CPC ¥2.50",
    "点击率 2.00%",
    "点击转化率 50.00%",
    "CPC · 去年同期 ¥2.50",
    "点击率 · 去年同期 2.00%",
    "点击转化率 · 去年同期 50.00%",
  ])
    expect(screen.getByText(text)).toBeTruthy();
});

it("卡片和漏斗的缺项点击率、转化率沿线上名称显示", async () => {
  const { Metrics, Trends } = await import("./Overview");
  const data = fixture();
  data.metrics = {
    ctr: { value: "0.02", status: "partial", type: "percent" } as never,
    cvr: { value: "0.5", status: "partial", type: "percent" } as never,
  };
  data.net = {};
  data.funnel = [];
  render(
    <>
      <Metrics data={data} selected={[]} toggle={() => {}} mode="product" />
      <Trends data={data} selected={["ctr", "cvr"]} product />
    </>,
  );
  expect(screen.getByRole("button", { name: /点击率\s*2.00%/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /点击转化率\s*50.00%/ })).toBeTruthy();
  expect(screen.getByText("↓ 点击率 2.00%")).toBeTruthy();
  expect(screen.getByText("↓ 点击转化率 50.00%")).toBeTruthy();
});

it("全空花费的本期不画ROI，去年有效值仍显示，换筛选恢复实线", () => {
  const data = fixture();
  for (const point of data.series) {
    point.metrics.roi = {
      value: null,
      status: "missing",
      reason: "当前范围未提供花费",
    } as never;
    point.previous.roi = { value: "3", status: "observed" } as never;
  }
  const { container, rerender } = render(<Trend data={data} selected={["roi"]} />);
  expect(container.querySelector('[data-series="roi-now"]')).toBeNull();
  expect(container.querySelector('[data-series="roi-prior"]')).not.toBeNull();
  expect(screen.queryByText("当前范围暂无推广数据")).toBeNull();
  for (const point of data.series) point.metrics.roi = { value: "2", status: "observed" } as never;
  rerender(<Trend data={{ ...data }} selected={["roi"]} />);
  expect(container.querySelector('[data-series="roi-now"]')).not.toBeNull();
  expect(screen.queryByText(/本期：部分记录/)).toBeNull();
});
