import { describe, expect, it } from "vitest";
import { reconcileSelection } from "./selection";
import { buildDefaultFilters } from "../state/filters";
import type { Options } from "../api/contracts";
const options: Options = {
  shops: [{ value: "outdoor", label: "户外" }],
  operators: ["乙"],
  categories: ["外套"],
  levels: ["新品"],
  products: [],
  dateRange: { from: "", to: "" },
};
describe("级联筛选", () => {
  it("换店后清除失效运营，暂不清空仍可能有效的下游品类", () => {
    expect(
      reconcileSelection(
        { ...buildDefaultFilters(), shop: "outdoor", operator: "甲", category: "外套" },
        options,
      ),
    ).toEqual({ operator: "" });
  });
  it("保留有效维度，移除新范围内不存在的商品", () => {
    expect(
      reconcileSelection(
        { ...buildDefaultFilters(), operator: "乙", category: "外套", products: ['["old","1"]'] },
        options,
      ),
    ).toEqual({ products: [] });
  });
});
