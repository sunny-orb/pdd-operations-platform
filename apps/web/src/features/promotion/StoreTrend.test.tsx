// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Overview } from "./contracts";
import { StoreTrend } from "./StoreTrend";

afterEach(cleanup);
function fixture(prior = true) {
  return {
    series: [1, 2, 3].map((day) => ({
      date: `2026-10-0${day}`,
      previous_date: `2025-10-0${day}`,
      // 有一家店无记录，但其它店当天仍有有效指标。
      missing: true,
      metrics: { revenue: { value: "123.45" }, roi: { value: "7.62" } },
      previous: {
        revenue: { value: prior ? "100.01" : null },
        roi: { value: prior ? "8.01" : null },
      },
    })),
  } as unknown as Overview;
}

it("单日真实零保留可见数据点，空数据不会伪造折线", () => {
  const data = fixture(false);
  data.series = [data.series[0]];
  data.series[0].metrics.revenue.value = "0";
  const { container, rerender } = render(<StoreTrend data={data} selected={["revenue"]} />);
  expect(container.querySelectorAll("circle")).toHaveLength(1);
  expect(container.querySelector("path[data-series]")?.getAttribute("d")).not.toMatch(
    /NaN|Infinity/,
  );
  rerender(<StoreTrend data={{ ...data, series: [] }} selected={["revenue"]} />);
  expect(container.querySelectorAll("path[data-series]")).toHaveLength(0);
});

it("两实两虚沿线上蓝橙配色；单店缺日仍画已有值，详情显示原始数值", () => {
  const { container } = render(<StoreTrend data={fixture()} selected={["revenue", "roi"]} />);
  const lines = container.querySelectorAll("path[data-series]");
  expect(lines).toHaveLength(4);
  expect(lines[0].getAttribute("stroke")).toBe("#1768d9");
  expect(lines[0].getAttribute("d")).toContain("L");
  expect(lines[1].getAttribute("stroke-dasharray")).toBe("6 5");
  expect(lines[2].getAttribute("stroke")).toBe("#e88429");
  fireEvent.focus(screen.getByRole("button", { name: "查看2026-10-02推广数据" }));
  expect(within(screen.getByRole("group", { name: "交易额" })).getByText("¥123.45")).toBeTruthy();
  expect(screen.getByText("去年 2025-10-02")).toBeTruthy();
  expect(screen.getByText("¥100.01")).toBeTruthy();
});

it("只在指标无值处断线，不补零；没有去年记录不绘虚线", () => {
  const data = fixture(false);
  data.series[1].metrics.revenue.value = null;
  const { container } = render(<StoreTrend data={data} selected={["revenue", "roi"]} />);
  const lines = container.querySelectorAll("path[data-series]");
  expect(lines).toHaveLength(2);
  expect(lines[0].getAttribute("d")?.match(/M/g)).toHaveLength(2);
  expect(lines[0].getAttribute("d")).not.toContain("L");
  expect(lines[1].getAttribute("d")).toContain("L");
  expect(screen.getByText("所选期间暂无去年同期数据")).toBeTruthy();
  expect(container.textContent).not.toContain("¥-");
});

it("30日范围保留首尾日期并避免末尾两个标签挤在一起", () => {
  const data = fixture();
  data.series = Array.from({ length: 30 }, (_, i) => ({
    ...data.series[0],
    date: `2026-10-${String(i + 1).padStart(2, "0")}`,
  }));
  const { container } = render(<StoreTrend data={data} selected={["revenue"]} />);
  const labels = [...container.querySelectorAll("text[data-date-label]")];
  expect(labels[0].textContent).toBe("10-01");
  expect(labels.at(-1)?.textContent).toBe("10-30");
  for (let i = 1; i < labels.length; i++) {
    expect(
      Number(labels[i].getAttribute("x")) - Number(labels[i - 1].getAttribute("x")),
    ).toBeGreaterThanOrEqual(48);
  }
});

it("悬停定位一天并标记四个交点，移出收起；点击固定后移开仍保留，再点取消", () => {
  const { container } = render(<StoreTrend data={fixture()} selected={["revenue", "roi"]} />);
  const day = screen.getByRole("button", { name: "查看2026-10-02推广数据" });
  const other = screen.getByRole("button", { name: "查看2026-10-03推广数据" });
  const chart = screen.getByRole("img");
  fireEvent.mouseEnter(day);
  expect(container.querySelector("[data-cursor-line]")?.getAttribute("x1")).toBe("450");
  expect(container.querySelectorAll("[data-cursor-dot]")).toHaveLength(4);
  expect(screen.getByText("10月2日")).toBeTruthy();
  fireEvent.mouseEnter(other);
  expect(container.querySelector("[data-cursor-line]")?.getAttribute("x1")).toBe("835");
  fireEvent.mouseLeave(chart);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
  fireEvent.click(day);
  expect(day.getAttribute("aria-pressed")).toBe("true");
  fireEvent.mouseEnter(other);
  fireEvent.mouseLeave(chart);
  expect(container.querySelector("[data-cursor-line]")?.getAttribute("x1")).toBe("450");
  fireEvent.click(day);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
});

it("无值不画交点，零值照常显示；键盘可固定和取消，更换数据或指标清掉旧选择", () => {
  const data = fixture(false);
  data.series[1].metrics.revenue.value = "0";
  data.series[1].metrics.roi.value = null;
  const { container, rerender } = render(<StoreTrend data={data} selected={["revenue", "roi"]} />);
  const day = screen.getByRole("button", { name: "查看2026-10-02推广数据" });
  fireEvent.focus(day);
  expect(container.querySelectorAll("[data-cursor-dot]")).toHaveLength(1);
  expect(screen.getByRole("group", { name: "交易额" }).textContent).toMatch(
    /本期.*¥0.00.*去年.*暂无数据/,
  );
  fireEvent.keyDown(day, { key: "Enter" });
  fireEvent.blur(day);
  expect(day.getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(day, { key: "Escape" });
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
  fireEvent.keyDown(day, { key: " " });
  rerender(<StoreTrend data={fixture()} selected={["revenue", "roi"]} />);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
  rerender(<StoreTrend data={data} selected={["revenue", "roi"]} />);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
  fireEvent.focus(day);
  fireEvent.click(day);
  rerender(<StoreTrend data={data} selected={["roi"]} />);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
  rerender(<StoreTrend data={data} selected={["revenue", "roi"]} />);
  expect(container.querySelector("[data-cursor-line]")).toBeNull();
});
