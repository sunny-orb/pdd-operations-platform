import type { DomainState } from "./data";

export type UpdateSource = {
  id: string;
  name: string;
  kind?: "reference" | "target";
  date: string | null;
  success: string | null;
  attempt?: string | null;
  attemptStatus?: string | null;
  sourceStatus: string;
  locator?: string | null;
  sourceUpdated?: string | null;
  /** 接收时间不是任务成功时间，两者分开保留。 */
  received?: string | null;
};

export const sourceNames: Record<string, string> = {
  shop_daily: "店铺日经营",
  shop_ratings: "店铺评分",
  shop_channel_product_promotion: "店铺商品推广",
  shop_channel_star_shop: "官方明星店铺推广",
  shop_regions: "店铺后台地区交易",
  product_regions: "商品地区交易",
  shop_refunds: "店铺退款金额",
  sales_targets: "店铺 GMV 目标",
  product_daily: "商品日经营",
  product_promotion: "商品推广明细",
  profile_records: "商品资料",
};

/** 仅汇集已接入接口的来源记录；不据本地日期推断上游日期或完整性。 */
export function updateRows(states: readonly DomainState[]) {
  return states.flatMap((state) =>
    (state.meta?.updates ?? []).map((source) => ({
      ...source,
      key: `${state.id}:${source.id}`,
      queryError: state.statusError ?? null,
    })),
  );
}

export function updateState(row: UpdateSource & { queryError?: string | null }) {
  if (row.queryError) return { label: "状态读取失败", color: "error" };
  if (row.attemptStatus === "failed" || row.sourceStatus === "failed_retained")
    return { label: "最近同步失败", color: "error" };
  if (["running", "pending"].includes(row.attemptStatus ?? ""))
    return { label: "同步处理中", color: "processing" };
  if (row.sourceStatus === "new_input_pending_report")
    return { label: "等待重新分析", color: "warning" };
  if (["not_connected", "source_unavailable"].includes(row.sourceStatus))
    return { label: "尚无可用数据", color: "default" };
  return { label: "上游待检查", color: "default" };
}
