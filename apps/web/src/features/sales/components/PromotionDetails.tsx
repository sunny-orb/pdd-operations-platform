import type { SalesMetrics, FiltersResponse } from "../api/contracts";
import { localTime } from "../format";
import "./promotion-details.css";

type Channel = NonNullable<SalesMetrics["promotion_channels"]>[number];
const fieldNames = {
  spend: "花费",
  attributed_revenue: "推广成交额",
  clicks: "点击量",
  impressions: "曝光量",
};
const channelName = (code: string) =>
  code.endsWith("star_shop") ? "明星店铺（官方店）" : "商品推广";
const unavailable = (channel: Channel) =>
  channel.source.state === "not_connected" && !channel.latest_observed_date;
const missingRecords = (channel: Channel) =>
  channel.missing_shop_days > 0 || channel.record_issues?.some((x) => x.kind === "missing_record");
const missingField = (channel: Channel, field: keyof typeof fieldNames) =>
  (channel.null_counts[field] ?? 0) > 0 ||
  channel.record_issues?.some((x) => x.kind === "null_fields" && x.fields.includes(field));
const hasMissingData = (channel: Channel) =>
  unavailable(channel) ||
  missingRecords(channel) ||
  (Object.keys(fieldNames) as (keyof typeof fieldNames)[]).some((field) =>
    missingField(channel, field),
  );

/** 仅解释当前接口结果；字段缺失只提示依赖它的指标，不重算、不把零分母当数据异常。 */
function impactSummary(data: SalesMetrics, channels: Channel[]) {
  const affects = (...fields: (keyof typeof fieldNames)[]) =>
    channels.some(
      (channel) =>
        unavailable(channel) ||
        missingRecords(channel) ||
        fields.some((field) => missingField(channel, field)),
    );
  return [
    affects("spend") &&
      (data.promotion_spend == null ? "推广花费暂无数据" : "推广花费按已有金额汇总"),
    affects("clicks", "impressions") &&
      (data.promotion_ctr == null ? "点击率暂无数据" : "点击率按已有完整记录计算"),
    affects("spend", "attributed_revenue") &&
      (data.promotion_roi == null ? "ROI暂无数据" : "ROI按已有完整记录计算"),
  ]
    .filter(Boolean)
    .join("；");
}

/** 缺行与空字段按同店同类合并展示，日期来自当前筛选的接口结果，真实零不列异常。 */
function RecordIssues({ channel, name }: { channel: Channel; name: (id: string) => string }) {
  if (channel.source.state === "not_connected" && !channel.latest_observed_date) {
    return <p>{channelName(channel.code)}：暂无可用数据。</p>;
  }
  const groups = new Map<string, { shop: string; reason: string; dates: string[] }>();
  for (const issue of channel.record_issues ?? []) {
    const key = `${issue.shop_id}/${issue.kind}/${issue.fields.join(",")}`;
    const group = groups.get(key) ?? {
      shop: name(issue.shop_id),
      reason:
        issue.kind === "missing_record"
          ? "无推广记录"
          : `${issue.fields.map((f) => fieldNames[f]).join("、")}为空（有记录）`,
      dates: [],
    };
    group.dates.push(issue.date);
    groups.set(key, group);
  }
  if (!groups.size) {
    return hasMissingData(channel) ? (
      <p>{channelName(channel.code)}：部分记录或指标数据未提供，详细日期暂未取得。</p>
    ) : null;
  }
  return (
    <ul className="promotion-record-issues">
      {[...groups].map(([key, group]) => (
        <li key={key}>
          {group.shop} · {channelName(channel.code)} · {group.reason}：
          {group.dates.map((date, index) => (
            <span key={date}>
              {index > 0 && "、"}
              <time dateTime={date}>{date}</time>
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

/** 只展示影响本期指标的缺失或更新失败；正常数据及历史补充不单独占用页面。 */
export function PromotionDetails({
  data,
  sources,
  shops,
  shopName,
}: {
  data?: SalesMetrics;
  sources?: FiltersResponse["data"]["sources"];
  shops?: FiltersResponse["data"]["shops"];
  shopName?: string;
}) {
  if (!data) return null;
  const channels = (data.promotion_channels ?? []).map((channel) => ({
    ...channel,
    source: { ...channel.source, ...sources?.[channel.code] },
  }));
  const missing = channels.filter(hasMissingData);
  const failed = channels.filter((channel) =>
    ["failed", "abandoned"].includes(channel.source.last_attempt_status ?? ""),
  );
  if (!missing.length && !failed.length) return null;
  const name = (id: string) => shops?.find((shop) => shop.shop_id === id)?.display_name ?? id;

  return (
    <aside className="promotion-source sales-promotion-details" aria-label="推广数据提示">
      {missing.length > 0 && (
        <p className="promotion-impact">本期推广数据不完整：{impactSummary(data, missing)}。</p>
      )}
      {failed.map((channel) => (
        <p key={channel.code} className="source-state-failed">
          {channelName(channel.code)}：最近检查未成功，
          {channel.source.last_success_at ? "当前显示上次成功数据。" : "暂无可用数据。"}
        </p>
      ))}
      <details className="promotion-details">
        <summary>查看异常明细</summary>
        <p className="promotion-period">
          {data.coverage.window_start} 至 {data.coverage.window_end}
          {shopName && ` · ${shopName}`}
        </p>
        {missing.map((channel) => (
          <RecordIssues key={channel.code} channel={channel} name={name} />
        ))}
        {failed.map((channel) => (
          <p key={channel.code}>
            {channelName(channel.code)}
            {channel.source.last_attempt_at &&
              ` · 最近检查时间：${localTime(channel.source.last_attempt_at)}`}
            {channel.source.last_success_at &&
              ` · 上次成功时间：${localTime(channel.source.last_success_at)}`}
            {!channel.source.last_attempt_at &&
              !channel.source.last_success_at &&
              " · 检查未成功，检查时间暂未取得"}
          </p>
        ))}
      </details>
    </aside>
  );
}
