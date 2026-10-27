import { expect, it } from "vitest";
import { format, percent, formatSignal } from "./format";
it("preserves large exact money, genuine zero and missing values", () => {
  expect(format("9007199254740993.12", "currency")).toBe("¥9,007,199,254,740,993");
  expect(formatSignal("9007199254740993.12", "currency")).toBe("¥9,007,199,254,740,993.12");
  expect(formatSignal("1200", "number")).toBe("1,200");
  expect(format("0", "number")).toBe("0");
  expect(format(null, "number")).toBe("—");
  expect(percent("0.123456")).toBe("12.3%");
});
it("matches prototype overview precision while score inputs retain displayed decimals", () => {
  expect(format("1234.56", "currency")).toBe("¥1,235");
  expect(format("0.123456", "percent")).toBe("12.35%");
});
