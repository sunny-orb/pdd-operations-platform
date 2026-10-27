import { expect, it } from "vitest";
import { formatClue } from "./format";
it("formats precise diagnosis evidence for people without long unbroken raw ratios", () => {
  expect(
    formatClue({
      label: "CTR",
      previous: "0.076041483271462815",
      current: "0.07",
      change: "-0.079449",
      evidence: "raw",
    }),
  ).toBe("7.60% → 7.00%（-7.94%）");
  expect(
    formatClue({
      label: "CPC",
      previous: "0.256462647173681777",
      current: "0.3",
      change: "0.16976",
      evidence: "raw",
    }),
  ).toBe("¥0.26 → ¥0.30（+16.98%）");
});
