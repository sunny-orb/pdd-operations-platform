import { params } from "./api";
import { expect, it } from "vitest";
import { readScope, patchScope, resetScope } from "./state";
import { formatValue } from "./format";
it("重点来自URL，默认报告随最新，自选报告不被替换", () => {
  const search = new URLSearchParams("focus=quality&view=style");
  expect(readScope(search, "r1")).toMatchObject({ report: "r1", focus: "quality", view: "style" });
  expect(readScope(search, "r2").report).toBe("r2");
  expect(readScope(patchScope(search, { report: "r1" }), "r2").report).toBe("r1");
});
it("筛选重置页码，重置保留标签和无关参数", () => {
  const search = new URLSearchParams(
    "report_id=r1&category=帽子&keyword=x&focus=size&view=quality&page=3&source=cockpit",
  );
  expect(readScope(patchScope(search, { category: "裤子" }), "r2").page).toBe(1);
  const reset = resetScope(search);
  expect(readScope(reset, "r2")).toMatchObject({
    report: "r2",
    category: "",
    keyword: "",
    focus: "all",
    view: "quality",
    page: 1,
  });
  expect(reset.get("source")).toBe("cockpit");
});
it("缺失不变成零，金额十进制精确显示", () => {
  expect(formatValue(null, "currency")).toBe("—");
  expect(formatValue("0", "currency")).toBe("¥0.00");
  expect(formatValue("9007199254740993.12", "currency")).toBe("¥9,007,199,254,740,993.12");
  expect(formatValue("0.1234", "percent")).toBe("12.34%");
  expect(formatValue("3.2", "points")).toBe("3.20个百分点");
});

it("默认报告只供展示，只有明确选择才固定请求报告", () => {
  const search = new URLSearchParams("view=history");
  expect(params(readScope(search, "r-latest"), "v1").has("report_id")).toBe(false);
  expect(
    params(readScope(patchScope(search, { report: "r-latest" }), "r-latest"), "v1").get(
      "report_id",
    ),
  ).toBe("r-latest");
});
