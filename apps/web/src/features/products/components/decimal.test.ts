import { describe, expect, it } from "vitest";
import { displayChange, displayValue } from "./display";

describe("真实十进制展示边界", () => {
  it("百分比与ROI保留超安全整数的小数，不转float", () => {
    expect(displayValue("9007199254740993.125", "ratio")).toBe("9007199254740993.13×");
    expect(displayValue("90071992547409.93125", "percent")).toBe("9007199254740993.13%");
    expect(displayChange("0.0005")).toBe("+0.1%");
  });
  it("负小数符号、科学十进制表示、NULL与零正确区分", () => {
    expect(displayValue("-0.05")).toBe("-0.05");
    expect(displayValue("1E-7", "percent")).toBe("0.00%");
    expect(displayValue("0E+8", "money")).toBe("¥0.00");
    expect(displayValue(null, "money")).toBe("—");
    expect(displayValue("0", "money")).toBe("¥0.00");
  });
});
