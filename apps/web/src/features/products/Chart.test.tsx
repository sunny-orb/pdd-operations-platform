// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { Line, LineChart } from "recharts";
import { afterEach, expect, it } from "vitest";
import { MiniTrend } from "./Chart";

afterEach(cleanup);

function points(container: HTMLElement) {
  return Array.from(container.querySelectorAll("polyline")).flatMap((line) =>
    line
      .getAttribute("points")!
      .split(" ")
      .map((point) => point.split(",").map(Number)),
  );
}

it("小幅上涨保持零基线，不将100到101放大成满幅上涨", () => {
  const { container } = render(<MiniTrend values={["100", "101"]} />);
  expect(points(container)).toEqual([
    [5, 8],
    [103, 7.85],
  ]);
});

it("恒定正值与真实零值保留不同的纵轴位置", () => {
  const { container, rerender } = render(<MiniTrend values={["100", "100"]} />);
  expect(points(container).map((point) => point[1])).toEqual([5, 5]);
  rerender(<MiniTrend values={["0", "0"]} />);
  expect(points(container).map((point) => point[1])).toEqual([23, 23]);
});

it.each([
  ["100", "101"],
  ["100", "100"],
  ["0", "0"],
  ["0", "1", "2"],
  ["0.02", "0.021", "0.018"],
  ["50", "45", "40", "30", "20", "10", "0"],
  ["-2", "-1", "0", "3"],
  ["-3", "-2", "-1"],
])("SVG坐标与原图实际绘制一致：%j", (...values) => {
  // 原图只移除负责测量容器的ResponsiveContainer，使用页面现有108×28尺寸。
  const original = render(
    <LineChart width={108} height={28} data={values.map((value) => ({ value: Number(value) }))}>
      <Line dataKey="value" dot={false} isAnimationActive={false} connectNulls={false} />
    </LineChart>,
  );
  const path = original.container.querySelector(".recharts-line-curve")!.getAttribute("d")!;
  const expected = Array.from(path.matchAll(/[ML](-?[\d.e+-]+),(-?[\d.e+-]+)/g), (match) => [
    Number(match[1]),
    Number(match[2]),
  ]);
  expect(expected).toHaveLength(values.length);
  const svg = render(<MiniTrend values={values} />);
  const actual = points(svg.container);
  expect(actual).toHaveLength(expected.length);
  actual.forEach((point, i) => {
    expect(point[0]).toBeCloseTo(expected[i][0], 3);
    expect(point[1]).toBeCloseTo(expected[i][1], 3);
  });
  expect(svg.container.querySelector(".recharts-wrapper")).toBeNull();
});

it("缺失日保持原横坐标和断线，不补零或连接缺口", () => {
  const { container } = render(<MiniTrend values={["100", "101", null, "0", "1"]} />);
  expect(container.querySelectorAll("polyline")).toHaveLength(2);
  expect(points(container)).toEqual([
    [5, 8],
    [29.5, 7.85],
    [78.5, 23],
    [103, 22.85],
  ]);
});

it("全缺失保持空图，孤立的真实零值仍可显示", () => {
  const { container, rerender } = render(<MiniTrend values={[null, null, null]} />);
  expect(container.querySelector("svg")).toBeNull();
  rerender(<MiniTrend values={[null, "0", null]} />);
  expect(container.querySelector("polyline")).toBeNull();
  expect(container.querySelector("circle")?.getAttribute("cx")).toBe("54");
  expect(container.querySelector("circle")?.getAttribute("cy")).toBe("23");
});
