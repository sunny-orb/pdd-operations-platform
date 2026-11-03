import type { FiltersResponse, SalesMetrics } from "../api/contracts";
import { formatFixedDecimal } from "../../../shared/format/decimal";
import { SalesExplanation } from "./SalesExplanation";
import "./sales-notes.css";

const basisLabels: Record<string, string> = {
  飞书历史补齐: "已核实补充",
  原版停投确认记录: "已确认未投放",
};

/** 仅展示当前范围内API标记生效的补充；不参与选源或计算，不从零金额推断未投放。 */
export function SalesNotes({
  data,
  shops,
  shopId,
}: {
  data?: SalesMetrics;
  shops?: FiltersResponse["data"]["shops"];
  shopId: string;
}) {
  const records = data?.promotion_supplements
    ?.filter(
      (row) =>
        row.status === "supplement" &&
        row.date >= data.coverage.window_start &&
        row.date <= data.coverage.window_end &&
        (!shopId || row.shop_id === shopId),
    )
    .sort((a, b) => a.date.localeCompare(b.date) || a.shop_id.localeCompare(b.shop_id));
  return (
    <SalesExplanation title="查看统计口径与历史补充" className="sales-notes">
      <ul className="sales-notes-definitions">
        <li>访客、买家按每天的数据累计。</li>
        <li>多店、多日的数据先汇总，再计算比率。</li>
        <li>推广成交金额是平台归因到推广渠道的成交金额，按支付日统计。</li>
      </ul>
      {records?.length ? (
        <div className="sales-notes-table" role="region" aria-label="本期历史补充记录" tabIndex={0}>
          <table aria-label="本期实际使用的历史补充">
            <thead>
              <tr>
                <th scope="col">店铺</th>
                <th scope="col">日期</th>
                <th scope="col">推广花费</th>
                <th scope="col">推广成交金额</th>
                <th scope="col">补充依据</th>
              </tr>
            </thead>
            <tbody>
              {records.map((row) => (
                <tr key={`${row.shop_id}/${row.date}`}>
                  <th scope="row">
                    {shops?.find((shop) => shop.shop_id === row.shop_id)?.display_name ??
                      row.shop_name}
                  </th>
                  <td>
                    <time dateTime={row.date}>{row.date}</time>
                  </td>
                  <td>¥{formatFixedDecimal(row.spend, 2, 0, true)}</td>
                  <td>¥{formatFixedDecimal(row.revenue, 2, 0, true)}</td>
                  <td>{basisLabels[row.source] ?? (row.source || "依据未提供")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>{records ? "本期未使用历史补充记录" : "暂未取得本期补充记录"}</p>
      )}
    </SalesExplanation>
  );
}
