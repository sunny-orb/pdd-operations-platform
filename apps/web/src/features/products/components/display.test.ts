import { describe, expect, it } from "vitest";
import { displayValue, displayChange } from "./display";

describe("经营展示边界", () => {
  it("区分真实零值和不可计算，金额不参与浮点汇总", () => {
    expect(displayValue(null, "money")).toBe("—");
    expect(displayValue("0", "money")).toBe("¥0.00");
    expect(displayValue("1234567.89", "money")).toBe("¥1,234,567.89");
    expect(displayValue("0.0345", "percent")).toBe("3.45%");
  });
  it("增长允许超过100%和负数，缺基数不是零增长", () => {
    expect(displayChange(null)).toBe("数据不足");
    expect(displayChange(1.25)).toBe("+125.0%");
    expect(displayChange(-0.25)).toBe("-25.0%");
    expect(displayChange(0)).toBe("0.0%");
  });
});
