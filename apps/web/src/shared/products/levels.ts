/** 商品等级的展示和轻量目录匹配；不参与评分、风险资格或金额计算。 */
type LevelInfo = {
  level?: string | null;
  level_group?: string;
  level_details?: string[];
};

export function formatProductLevel(value: string | null | undefined, details?: string[]) {
  const members = details ?? (value?.endsWith("混合") ? value.slice(0, -2).split("/") : []);
  return members.length > 1 ? `多等级（含${members.join("、")}）` : value || "未匹配";
}

export function productLevelGroup(item: LevelInfo) {
  return item.level_group ?? item.level ?? "未匹配";
}

/** 新分组和旧精确组合并存，不能把旧条件悄悄扩大成全部多等级。 */
export function matchesProductLevel(item: LevelInfo, wanted: string) {
  const actual = ["多等级", "未匹配"].includes(wanted) ? productLevelGroup(item) : item.level;
  return !wanted || actual === wanted;
}
