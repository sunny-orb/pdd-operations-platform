/** 将受控技术状态翻译成运营能理解的原因；未知代码保守提示，不猜业务原因。 */
const labels: Record<string, string> = {
  source_completeness_unknown: "来源完整日尚未确认",
  current_profile_view: "按当前资料归属",
  definition_pending: "收藏日值定义待确认",
  favorite_definition_pending: "收藏日值定义待确认",
  outside_imported_range: "部分日期尚未入库",
  no_records_received: "该日期尚未收到数据",
  null_source_value: "部分源字段暂无值",
  missing_record_dates: "部分店铺日期尚未收到数据",
  missing_product_dates: "部分商品日期尚未收到数据",
  invalid_denominator: "分母无效，暂不可计算",
  source_complete_windows_unverified: "完整双7日窗口尚未证明",
  thresholds_trial_or_not_validated: "固定显示基线试算，标准尚未正式验收",
  promotion_attribution_description_pending: "推广归因日期及窗口说明待补",
  no_applicable_threshold: "尚无适用四档标准",
  missing_or_invalid_value: "该项指标缺少有效值",
  nonpositive_denominator: "分母须大于零",
  nonpositive_previous_revenue: "前7日金额未形成正基数",
  growth_base_floor_missing: "尚无适用增长基数线",
  previous_orders_below_5: "前7日订单不足5单",
  previous_revenue_below_growth_floor: "前7日金额未达到增长基数线",
  ad_impressions_below_2000: "近7日曝光不足2000，待推广验证",
  threshold_profile_category_conflict: "当前品类资料冲突，历史标准暂不适用",
  threshold_profile_category_changed: "当前品类已变化，历史标准暂不适用",
  threshold_from_future: "标准晚于本次评分日期，暂不适用",
  no_compatible_score_publication: "当前输入对应的评分尚未发布",
  score_model_not_configured: "评分标准尚未入库",
  negative_input: "存在负值，暂不判分",
  insufficient_evidence: "证据不足，暂不判断",
  not_computable: "暂不可计算",
  revenue_multiple_7d: "增长项待验证",
  favorites_7d: "收藏项待验证",
  ad_roi_7d: "投产未达建议条件",
  score_conversion_rate_7d: "评分转化未达建议条件",
  ad_click_conversion_rate_7d: "点击转化未达建议条件",
};
export function reasonText(code: string): string {
  if (labels[code]) return labels[code];
  if (code.endsWith("_missing_dates"))
    return code.startsWith("previous") ? "前7日存在缺日" : "近7日存在缺日";
  if (code.endsWith("_no_records_received")) return "对应窗口尚未收到数据";
  if (code.endsWith("_missing_values")) return "部分必要源字段暂无值";
  if (code.endsWith("_negative_input")) return "必要输入存在负值，暂不计分";
  if (code.endsWith("_outside_imported_range")) return "对应窗口部分日期尚未入库";
  return /[\u3400-\u9fff]/.test(code) ? code : "该项依据不足，待核实";
}
export const reasonList = (values: string[]) => [...new Set(values.map(reasonText))];
