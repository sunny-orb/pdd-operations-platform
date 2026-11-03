import { memo } from "react";
import type { z } from "zod";
import type { FiltersResponse } from "../api/contracts";
import type { regionSchema } from "../api/details";
import { localTime } from "../format";
import { SalesExplanation } from "./SalesExplanation";

/** 只解释本区结果；无记录按约定列为未销售，不改金额，也不把空字段或请求失败归入未销售。 */
export const ProductRegionNotes = memo(function ProductRegionNotes({
  data,
  shops,
  source,
}: {
  data: z.infer<typeof regionSchema>["data"] | undefined;
  shops: FiltersResponse["data"]["shops"];
  source: { last_success_at?: string | null; last_attempt_status?: string | null } | undefined;
}) {
  const currentSource = source ?? data?.source;
  const missing = new Map<string, string[]>();
  if (data?.source.state === "observed") {
    for (const value of data.missing_shop_days) {
      const [id, date] = value.split(":");
      missing.set(id, [...(missing.get(id) ?? []), date]);
    }
  }
  const failed = ["failed", "abandoned"].includes(currentSource?.last_attempt_status ?? "");
  return (
    <SalesExplanation title="查看聚水潭口径" className="product-statistics-notes">
      <ul>
        <li>
          <b>统计范围：</b>
          按本区日期、店铺、品类和产品汇总。实发金额占比＝地区实发金额÷本期实发总金额。
        </li>
        <li>
          <b>退款率：</b>
          实退金额合计÷实发金额合计，反映所选期间的金额比例；实退与实发可能来自不同批次的订单。
        </li>
        <li>
          <b>空值处理：</b>
          金额分别累计已有值，部分缺失标“*”；任一金额全空或实发合计≤0时，退款率显示“—”。
        </li>
        <li>
          <b>产品归类：</b>
          同品类按款式合并颜色、规格；无款式时按商品编码区分。两码都缺的仅计入汇总；排除包材、儿童赠品和“分类未设置”。
        </li>
        <li>
          <b>查找产品：</b>输入名称或编码会清除已选产品、缩小候选范围，重新选中产品后再统计该产品。
        </li>
      </ul>
      {failed && (
        <p role="status">
          {currentSource?.last_success_at
            ? "本次同步失败，保留上次成功值。"
            : "本次同步失败，尚无成功数据。"}
        </p>
      )}
      {missing.size > 0 && (
        <div className="product-no-sales">
          <p>未销售日期（按当前筛选下无记录统计）</p>
          <dl>
            {[...missing].map(([id, dates]) => (
              <div key={id}>
                <dt>{shops.find((shop) => shop.shop_id === id)?.display_name ?? id}</dt>
                <dd>{dates.join("、")}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <p className="product-notes-source">
        来源：聚水潭地区交易 · 数仓 bwd_rpa_pdd_region_sales_summary_jst_f
        <br />
        最近同步成功：{localTime(currentSource?.last_success_at)}
        {currentSource?.last_attempt_status === "running" && " · 正在同步"}
      </p>
    </SalesExplanation>
  );
});
