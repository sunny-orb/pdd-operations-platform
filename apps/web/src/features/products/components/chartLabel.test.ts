import { describe, expect, it } from "vitest";
import { chartLabel } from "./chartLabel";
describe("图表百分比", () => {
  it("相对走势0.08显示8%而不是0.08%", () => {
    expect(chartLabel(0.08, "money", true)).toBe("8.00%");
    expect(chartLabel(0, "percent", true)).toBe("0.00%");
  });
});
