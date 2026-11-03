import { memo } from "react";
import type { Board } from "./contracts";
import { change, format } from "./format";

/** 展示后端规则结论及同一响应的七卡值；指标切换只改变底部关注数值。 */
export const ProductDiagnosis = memo(function ProductDiagnosis({
  data,
  metric,
}: {
  data?: Board;
  metric: string;
}) {
  const diagnosis = data?.diagnosis;
  const selected = data?.metrics[metric];
  const revenue = data?.metrics.revenue;
  const visitors = data?.metrics.visitors;
  const conversion = data?.metrics.conversion;
  const yoy = (value?: Board["metrics"][string]) => {
    if (value?.yoy != null) return `同比${change(value.yoy)}`;
    if (value?.previous != null && Number(value.previous) === 0) return "去年同期为0，暂不计算同比";
    return "暂无同比结果";
  };
  return (
    <aside className="panel insights product-diagnosis" aria-label="经营诊断">
      <span>规则诊断</span>
      <h2>当前筛选范围的经营诊断</h2>
      {data && (
        <small className="product-diagnosis-period">
          {data.scope.start} 至 {data.scope.end}
        </small>
      )}
      <div className="diagnosis-list">
        <div className="diagnosis-item">
          <b>{diagnosis?.growth_title ?? "交易表现"}</b>
          <p>
            交易金额{yoy(revenue)}，当前为{format(revenue?.value, "currency")}。
          </p>
        </div>
        <div className="diagnosis-item">
          <b>{diagnosis?.traffic_title ?? "流量变化"}</b>
          <p>
            商品访客数{yoy(visitors)}，支付转化率{yoy(conversion)}。
          </p>
        </div>
        <div className="diagnosis-item">
          <b>优先动作</b>
          <p>{diagnosis?.action ?? "暂无可用建议。"}</p>
        </div>
      </div>
      <div className="mini-stat">
        <b>{format(selected?.value, selected?.type ?? "currency")}</b>
        <span>当前{selected?.label ?? "交易金额"}</span>
      </div>
    </aside>
  );
});
