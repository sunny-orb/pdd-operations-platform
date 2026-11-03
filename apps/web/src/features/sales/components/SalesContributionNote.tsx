import type { FiltersResponse, ShopItem } from "../api/contracts";
import { REFUND_SOURCE } from "../sourceLinks";

/** 汇总六店当前窗口的来源覆盖；记录为0也计数，历史补充与数仓记录分开。 */
function promotionScope(shops: ShopItem[]) {
  if (shops.some((shop) => !shop.promotion_channels || !shop.promotion_supplements))
    return "暂未取得推广范围";
  let warehouseShops = 0;
  const supplements = new Set<string>();
  const starShops = new Set<string>();
  for (const shop of shops) {
    const { window_start: start, window_end: end } = shop.coverage;
    const used = new Set(
      shop
        .promotion_supplements!.filter(
          (row) =>
            row.status === "supplement" &&
            row.shop_id === shop.shop_id &&
            row.date >= start &&
            row.date <= end,
        )
        .map((row) => row.date),
    );
    for (const day of used) supplements.add(`${shop.shop_id}/${day}`);
    for (const channel of shop.promotion_channels!) {
      if (channel.latest_observed_date === undefined) return "暂未取得推广范围";
      if (!channel.latest_observed_date) continue;
      if (channel.code === "shop_channel_product_promotion") {
        const days =
          (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000 + 1;
        // 单店渠道的应有天数减缺记录天数为生效记录数；扣掉生效补充后才是数仓记录。
        if (channel.source.state === "observed" && days - channel.missing_shop_days > used.size)
          warehouseShops++;
      } else if (channel.code === "shop_channel_star_shop") {
        starShops.add(shop.display_name);
      }
    }
  }
  const parts = [
    ...(warehouseShops ? [`数仓 ${warehouseShops} 店`] : []),
    ...Array.from(starShops, (name) => `${name.replace(/店$/, "")}明星店铺`),
    ...(supplements.size ? [`历史补充 ${supplements.size} 条`] : []),
  ];
  if (parts.length) return parts.join(" + ");
  return shops.some((shop) =>
    shop.promotion_channels!.some((channel) => channel.source.state === "observed"),
  )
    ? "本期暂无推广记录"
    : "暂无可用推广数据";
}

function feishuSource(source?: { state: string }) {
  return source?.state === "observed"
    ? "飞书多维表"
    : source?.state === "not_connected"
      ? "暂无可用数据"
      : "来源状态暂未取得";
}

/** 与左侧排名共用全店、同日期结果；仅展示来源覆盖，不重新计算经营指标。 */
export function SalesContributionNote({
  shops,
  sources,
  error,
}: {
  shops: ShopItem[];
  sources?: FiltersResponse["data"]["sources"];
  error: string | null;
}) {
  const salesShops = shops.filter((shop) => shop.coverage.observed_days > 0).length;
  const pending = error ? "读取失败，请重试" : "正在读取…";
  const refund = feishuSource(sources?.shop_refunds);
  const target = feishuSource(sources?.sales_targets);
  const period = shops[0]?.coverage;
  return (
    <aside className="data-note" aria-label="经营数据说明">
      <h3>经营数据说明</h3>
      <p className="contribution-period">
        {period ? (
          <>
            <time dateTime={period.window_start}>{period.window_start}</time>
            {period.window_end !== period.window_start && (
              <>
                {" "}
                至 <time dateTime={period.window_end}>{period.window_end}</time>
              </>
            )}
            {" · 全部店铺"}
          </>
        ) : (
          pending
        )}
      </p>
      <p>销售、访客、买家和订单来自数仓，按日累计。销售占比＝本店销售额÷全部店铺销售额。</p>
      <dl>
        <div>
          <dt>销售数据</dt>
          <dd>
            {shops.length ? (salesShops ? `${salesShops} 家店铺` : "本期暂无销售记录") : pending}
          </dd>
        </div>
        <div>
          <dt>推广数据</dt>
          <dd>{shops.length ? promotionScope(shops) : pending}</dd>
        </div>
        <div>
          <dt>退款 / 目标</dt>
          <dd>{refund === target ? refund : `退款：${refund}；目标：${target}`}</dd>
        </div>
      </dl>
      <p>退款金额占比只计算同店同日销售、退款金额都有值的记录，缺项见上方“计算明细”。</p>
      <p>推广优先使用数仓记录，缺记录时采用已确认的历史补充，不重复累计。</p>
      <a href={REFUND_SOURCE} target="_blank" rel="noreferrer">
        查看统计退款金额 ↗
      </a>
    </aside>
  );
}
