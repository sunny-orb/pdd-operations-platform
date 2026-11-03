import { memo } from "react";
import { Button, Popover } from "antd";
import type { FiltersResponse, SalesMetrics, ShopItem } from "../api/contracts";
import { decimalParts, formatFixedDecimal } from "../../../shared/format/decimal";
import { localTime, percent } from "../format";

type Props = {
  data?: SalesMetrics;
  shops: ShopItem[];
  shopId: string;
  shopName: string;
  rangeLabel: string;
  source?: NonNullable<FiltersResponse["data"]["sources"]>[string];
};
const amount = (value: string | null) =>
  value == null ? "—" : `¥${formatFixedDecimal(value, 2, 0, true)}`;

/** 只解释API已排除的同店日期；用覆盖字段区分空金额与缺整行，不自行扩大排除范围。 */
function excludedRecords(shops: ShopItem[], shopId: string) {
  return shops
    .filter((shop) => !shopId || shop.shop_id === shopId)
    .flatMap((shop) => {
      const groups = new Map<string, string[]>();
      for (const date of shop.refund_match?.missing_dates ?? []) {
        const coverage = shop.refund_coverage;
        const reason =
          coverage?.state === "not_connected"
            ? "退款数据暂不可用"
            : coverage?.null_amount_dates.includes(date)
              ? "退款金额为空（有记录）"
              : coverage?.missing_record_dates.includes(date)
                ? "无退款记录"
                : "退款金额未取得";
        groups.set(reason, [...(groups.get(reason) ?? []), date]);
      }
      return [...groups].map(([reason, dates]) => ({ shop: shop.display_name, reason, dates }));
    });
}

/** 展示后端实际分子、分母和结果；弹层随当前范围更新，金额不取整、不重新计算比率。 */
export const RefundDetails = memo(function RefundDetails({
  data,
  shops,
  shopId,
  shopName,
  rangeLabel,
  source,
}: Props) {
  const match = data?.refund_match;
  const excluded = data ? excludedRecords(shops, shopId) : [];
  const status = { ...match?.source, ...source };
  const failed = ["failed", "abandoned"].includes(status.last_attempt_status ?? "");
  const sales = decimalParts(match?.matched_sales);
  const canShowFormula =
    data?.refund_rate != null && match?.matched_refunds != null && match.matched_sales != null;
  const content = (
    <section className="sales-refund-calculation" aria-label="退款金额占比计算明细">
      <p className="sales-refund-scope">
        {rangeLabel} · {shopName}
      </p>
      {canShowFormula ? (
        <p className="sales-refund-formula">
          退款金额 {amount(match.matched_refunds)} ÷ 销售金额 {amount(match.matched_sales)} ={" "}
          {percent(data.refund_rate)}
        </p>
      ) : (
        <p>
          {status.state === "not_connected"
            ? "退款数据暂不可用。"
            : sales && (sales.negative || sales.coefficient === 0n)
              ? "参与计算的销售金额未大于0元，暂无法计算。"
              : "本期暂无可用于计算的销售和退款金额。"}
        </p>
      )}
      <p>退款按发生日期统计；按店铺逐日核对，两项金额都有数据才参与计算。</p>
      {excluded.length > 0 && (
        <ul>
          {excluded.map((row) => (
            <li key={`${row.shop}/${row.reason}`}>
              {row.shop} ·{" "}
              {row.dates.map((date, index) => (
                <span key={date}>
                  {index > 0 && "、"}
                  <time dateTime={date}>{date}</time>
                </span>
              ))}
              ：{row.reason}，该店{row.dates.length === 1 ? "当天" : "这些日期"}未参与计算。
            </li>
          ))}
        </ul>
      )}
      {!excluded.length && (match?.excluded_days ?? 0) > 0 && (
        <p>有 {match!.excluded_days} 条店铺每日记录未参与计算，具体店铺和日期暂未取得。</p>
      )}
      {failed && (
        <p>
          退款数据更新未成功。
          {status.last_success_at && `上次成功时间：${localTime(status.last_success_at)}。`}
          {status.last_attempt_at && `最近检查时间：${localTime(status.last_attempt_at)}。`}
        </p>
      )}
    </section>
  );
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      content={content}
      classNames={{ root: "sales-refund-popover" }}
    >
      <Button className="sales-refund-trigger" type="link" size="small" disabled={!data}>
        计算明细
      </Button>
    </Popover>
  );
});
