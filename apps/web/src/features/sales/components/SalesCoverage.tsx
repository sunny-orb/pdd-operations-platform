import { Fragment } from "react";
import type { SalesResult } from "../api/contracts";
import { localTime } from "../format";

function Dates({ values }: { values: string[] }) {
  return values.map((date, index) => (
    <Fragment key={date}>
      {index > 0 && "、"}
      <span className="sales-coverage-date">{date}</span>
    </Fragment>
  ));
}

/** 仅展示当前筛选的记录覆盖；NULL、无记录和真实0分别保留，指标算法由接口负责。 */
export function SalesCoverage({
  result,
  error,
}: {
  result: SalesResult | null;
  error: string | null;
}) {
  const shops = result?.shops ?? [];
  const salesMissing = shops.filter((shop) => shop.coverage.missing_dates.length > 0);
  const refunds = shops.flatMap((shop) =>
    shop.refund_coverage?.state === "observed" ? [shop.refund_coverage] : [],
  );
  const emptyAmounts = refunds.filter((refund) => refund.null_amount_dates.length > 0);
  const absentRecords = refunds.filter((refund) => refund.missing_record_dates.length > 0);
  const unavailable = shops.length - refunds.length;
  const overview = [
    !shops.some((shop) => shop.coverage.observed_days > 0)
      ? "所选时段暂无销售记录"
      : salesMissing.length
        ? `${salesMissing.length} 家店缺少部分日期的销售记录`
        : "销售记录齐全",
    ...(emptyAmounts.length
      ? [
          `${emptyAmounts.length} 家店有 ${emptyAmounts.reduce((sum, refund) => sum + refund.null_amount_dates.length, 0)} 条退款金额为空的记录`,
        ]
      : []),
    ...(absentRecords.length
      ? [
          `${absentRecords.length} 家店缺少 ${absentRecords.reduce((sum, refund) => sum + refund.missing_record_dates.length, 0)} 条每日退款记录`,
        ]
      : []),
    ...(unavailable ? [`${unavailable} 家店退款数据暂不可用`] : []),
    ...(!emptyAmounts.length && !absentRecords.length && !unavailable && refunds.length
      ? ["退款金额均已提供"]
      : []),
  ];
  // 渠道日期只取所选期间实际记录；不回退到数据集全局最大日。
  const channels = shops.flatMap((shop) => shop.promotion_channels ?? []);
  const promotionDates = [...new Set(channels.map((channel) => channel.code))].map((code) => {
    const selected = channels.filter((channel) => channel.code === code);
    const dates = selected.flatMap((channel) =>
      channel.latest_observed_date ? [channel.latest_observed_date] : [],
    );
    return {
      code,
      name: code === "shop_channel_star_shop" ? "明星店铺" : "商品推广",
      latest: dates.sort().at(-1),
      empty: selected.every((channel) => channel.source.state === "not_connected")
        ? "暂不可用"
        : selected.every((channel) => channel.latest_observed_date === null)
          ? "暂无记录"
          : "未取得日期",
    };
  });
  const status = result?.meta.last_attempt_status;
  const failed = status === "failed" || status === "abandoned";
  const checkTime =
    status && ["failed", "abandoned", "running"].includes(status)
      ? (result?.meta.last_attempt_at ?? result?.meta.last_success_at)
      : result?.meta.last_success_at;
  return (
    <aside className="source-coverage" aria-label="数据覆盖说明">
      <strong>{result ? "所选时段数据情况" : error ? "暂时无法获取数据" : "正在加载数据…"}</strong>
      <p>{result ? `${overview.join("；")}。` : "数据加载完成前，指标暂不显示。"}</p>
      {failed && <p>销售数据检查未成功，当前保留上次检查成功的数据。</p>}
      {status === "running" && <p>正在检查销售数据，当前显示上次检查结果。</p>}
      <details>
        <summary>查看各店铺销售与退款数据明细</summary>
        {result && (
          <>
            <ul className="sales-coverage-shops" aria-label="各店铺数据情况">
              {shops.map((shop) => {
                const missing = shop.coverage.missing_dates.map((date) => date.split(":").at(-1)!);
                const refund = shop.refund_coverage;
                const known = refund?.state === "observed";
                return (
                  <li key={shop.shop_id}>
                    <b>{shop.display_name}</b>
                    <div className="sales-coverage-fields">
                      <span>
                        {missing.length ? (
                          <>
                            缺少 {missing.length} 天销售记录：
                            <Dates values={missing} />
                          </>
                        ) : shop.coverage.observed_days ? (
                          <>销售记录齐全，共 {shop.coverage.observed_days} 天</>
                        ) : (
                          "暂无销售记录"
                        )}
                      </span>
                      {!known ? (
                        <span>退款数据暂不可用</span>
                      ) : (
                        <>
                          {refund.null_amount_dates.length > 0 && (
                            <span>
                              退款金额为空（有记录）：
                              <Dates values={refund.null_amount_dates} />
                            </span>
                          )}
                          {refund.missing_record_dates.length > 0 && (
                            <span>
                              无退款记录：
                              <Dates values={refund.missing_record_dates} />
                            </span>
                          )}
                          {!refund.null_amount_dates.length &&
                            !refund.missing_record_dates.length && <span>退款金额均已提供</span>}
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
            {promotionDates.length > 0 && (
              <p className="sales-coverage-promotion">
                所选时段推广记录截至：
                {promotionDates.map((channel, index) => (
                  <Fragment key={channel.code}>
                    {index > 0 && " / "}
                    <span>
                      {channel.name}{" "}
                      {channel.latest ? <Dates values={[channel.latest]} /> : channel.empty}
                    </span>
                  </Fragment>
                ))}
              </p>
            )}
            <p>退款率仅统计销售和退款金额均有数据的日期。</p>
            <p>最近检查时间：{localTime(checkTime)}</p>
          </>
        )}
      </details>
    </aside>
  );
}
