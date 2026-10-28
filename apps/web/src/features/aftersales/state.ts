export type Focus = "all" | "size" | "quality";
export type View = "category" | "style" | "mom" | "yoy" | "quality" | "history";
export type Scope = {
  report: string;
  reportExplicit: boolean;
  category: string;
  keyword: string;
  focus: Focus;
  view: View;
  page: number;
  size: number;
};
export const views: { key: View; label: string }[] = [
  { key: "category", label: "品类分析" },
  { key: "style", label: "单款分析" },
  { key: "mom", label: "环比分析" },
  { key: "yoy", label: "同比分析" },
  { key: "quality", label: "质量问题" },
  { key: "history", label: "历史记录" },
];
/** 缺省报告不写入URL，随已发布目录更新；用户明确选择的报告始终保留。 */
export function readScope(search: URLSearchParams, latest: string): Scope {
  const focus = search.get("focus");
  const view = search.get("view");
  const page = Number(search.get("page"));
  const size = Number(search.get("size"));
  return {
    report: search.get("report_id") || latest,
    reportExplicit: !!search.get("report_id"),
    category: search.get("category") || "",
    keyword: search.get("keyword") || "",
    focus: focus === "size" || focus === "quality" ? focus : "all",
    view: views.some((v) => v.key === view) ? (view as View) : "category",
    page: Number.isSafeInteger(page) && page > 0 ? page : 1,
    size: [25, 50, 100].includes(size) ? size : 25,
  };
}
export function patchScope(search: URLSearchParams, patch: Partial<Scope>) {
  const result = new URLSearchParams(search);
  if (patch.page === undefined) result.delete("page");
  Object.entries(patch).forEach(([key, value]) => {
    if (key === "reportExplicit") return;
    const name = key === "report" ? "report_id" : key;
    if (value === "" || (key === "focus" && value === "all")) result.delete(name);
    else result.set(name, String(value));
  });
  return result;
}
export function resetScope(search: URLSearchParams) {
  const result = new URLSearchParams(search);
  ["report_id", "category", "keyword", "focus", "page", "size"].forEach((key) =>
    result.delete(key),
  );
  return result;
}
