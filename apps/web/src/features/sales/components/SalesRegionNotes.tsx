import type { z } from "zod";
import type { FiltersResponse } from "../api/contracts";
import type { regionSchema } from "../api/details";
import { SalesExplanation } from "./SalesExplanation";

type RegionData = z.infer<typeof regionSchema>["data"];
const fieldNames: Record<string, string> = {
  transaction_amount: "成交金额",
  transaction_orders: "成交订单数",
  transaction_buyers: "成交买家数",
};

/** 只展示当前查询的缺行/空字段，真实0不列异常；不在展示层重算经营指标。 */
export function SalesRegionNotes({
  data,
  shops,
  source,
}: {
  data: RegionData | undefined;
  shops: FiltersResponse["data"]["shops"];
  source: { last_attempt_status?: string | null } | undefined;
}) {
  if (!data || data.source.state !== "observed") return null;
  const name = (id: string) => shops.find((shop) => shop.shop_id === id)?.display_name ?? id;
  const missing = new Map<string, string[]>();
  for (const value of data.missing_shop_days) {
    const [shop, day] = value.split(":");
    missing.set(shop, [...(missing.get(shop) ?? []), day]);
  }
  const emptyFields = Object.entries(fieldNames).filter(([key]) => data.null_counts[key] > 0);
  const summary = [
    ...(missing.size ? [`${missing.size}家店有日期暂无地区交易记录`] : []),
    ...(emptyFields.length
      ? [`${emptyFields.map(([, label]) => label).join("、")}有未提供的记录`]
      : []),
  ];
  const failed = ["failed", "abandoned"].includes(source?.last_attempt_status ?? "");
  return (
    <div className="region-notes">
      {failed && (
        <p role="status">
          本次更新未成功，当前显示上次成功数据（数据截至{data.source.observed_through ?? "—"}）。
        </p>
      )}
      {data.items.length > 0 && summary.length > 0 && (
        <p className="region-data-notice" aria-label="地区交易数据提示">
          {summary.join("；")}。已按已有数据统计，详情见下方。
        </p>
      )}
      <SalesExplanation title="查看地区交易明细与口径">
        {[...missing].map(([id, dates]) => (
          <p key={id}>
            <b>{name(id)}</b> · 暂无记录：{dates.join("、")}
          </p>
        ))}
        {data.field_gaps?.map((gap) => (
          <p key={`${gap.shop_id}:${gap.date}`}>
            <b>{name(gap.shop_id)}</b> · {gap.date} ·{" "}
            {Object.entries(gap.fields)
              .filter(([, n]) => n > 0)
              .map(([field, n]) => `${fieldNames[field] ?? field}未提供：${n}条地区记录`)
              .join("；")}
          </p>
        ))}
        {emptyFields.length > 0 && !data.field_gaps?.length && (
          <p>缺项的店铺、日期明细暂未取得。</p>
        )}
        <ul>
          <li>金额、订单和买家数按本区日期及店铺累计；买家数不跨店、跨日去重。</li>
          <li>销售额占比＝地区成交金额÷本期地区成交总金额；总金额不大于0时显示“—”。</li>
          <li>客单价＝成交金额合计÷买家数合计，只计两项都有值的记录；分母不大于0时显示“—”。</li>
        </ul>
        <p>
          来源：拼多多后台地区交易（数仓 bwd_rpa_pdd_region_sales_summary_pddht_f） · 来源更新时间：
          {data.source.source_updated_text ?? "未提供"}
        </p>
      </SalesExplanation>
    </div>
  );
}
