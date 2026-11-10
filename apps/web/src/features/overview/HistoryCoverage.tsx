import { Tag } from "antd";
import type { HistoryCoverageStatus, SourceStatus } from "./sourceStatusApi";
import { pageNames } from "./sourceStatusApi";
import { localTime } from "./model";
import { sourceHasNoRecords } from "./dataUpdateAlerts";

const labels: Record<HistoryCoverageStatus["state"], string> = {
  gaps: "部分日期无记录",
  observed: "未发现日期缺口",
  pending: "等待覆盖检查",
  unavailable: "尚无可检查数据",
  unverified: "暂不能判断完整性",
  not_applicable: "不适用每日检查",
  failed: "覆盖检查失败",
  stale: "等待重新检查",
};

/** 仅展示服务端检查结果；不在浏览器重新推算日期、缺口或完整性。 */
export function HistoryCoverageLabel({ row }: { row: SourceStatus }) {
  const value = row.history;
  const state = value?.state ?? "pending";
  const hasNoRecords = sourceHasNoRecords(row);
  const recordHint = state === "gaps" ? "记录缺失" : "上次检查记录缺失";
  return (
    <div className="m6-update-cell-stack">
      <span className="m6-update-status-label">
        {hasNoRecords && (
          <span
            className="m6-update-row-dot m6-update-record-dot"
            role="img"
            aria-label={`${recordHint}：${row.name}`}
            title={`${recordHint}，展开明细查看店铺和日期`}
          />
        )}
        <Tag color={state === "failed" ? "error" : state === "stale" ? "warning" : "default"}>
          {labels[state]}
        </Tag>
      </span>
      {hasNoRecords && (
        <small className="m6-update-subtext">
          {value?.missing_date_count} 个日期 · {value?.missing_count} 个店铺日
          {state !== "gaps" && "（上次结果）"}
        </small>
      )}
    </div>
  );
}

export function HistoryCoverage({ row }: { row: SourceStatus }) {
  const h = row.history;
  if (!h) return null;
  return (
    <section className="m6-history-coverage" aria-label={`${row.name}历史记录覆盖`}>
      <strong>历史记录覆盖</strong>
      {h.state === "not_applicable" ? (
        <p>资料、目标或天气按各自修订及发布周期更新，不套用逐日记录检查。</p>
      ) : h.state === "unverified" ? (
        <p>
          此来源尚无可靠的每日应有记录范围，不能凭某日没有业务记录判定缺数，也不能据此认定数据完整。
        </p>
      ) : (
        <>
          {h.window_start && (
            <p>
              检查期间：{h.window_start} 至 {h.window_end}（北京时间）
            </p>
          )}
          {h.checked_through && h.checked_through !== h.window_end && (
            <p>{h.window_end} 在该次检查时尚未到约定检查时点，暂不计入缺口。</p>
          )}
          {h.state === "stale" && <p>版本或检查窗口已变化，下方保留上次结果，等待后台重新检查。</p>}
          {h.state === "failed" && (
            <p>
              覆盖检查未成功，{h.checked_at ? "下方保留上次检查结果。" : "暂不能判断历史缺口。"}
              已有经营数据仍可使用。
            </p>
          )}
          {(h.state === "pending" || h.state === "unavailable") && (
            <p>{labels[h.state]}，不能据此认定没有缺口。</p>
          )}
          {h.state === "observed" && (
            <p>本次检查未发现店铺日记录缺口；这不代表所有字段或商品记录均完整。</p>
          )}
          {(h.missing_count ?? 0) > 0 && (
            <>
              <p>
                {h.missing_date_count} 个日期没有对应记录，涉及 {h.groups?.length} 家店、
                {h.missing_count} 个店铺日。使用页面：
                {row.pages.map((p) => pageNames[p]).join("、")}。
              </p>
              <ul>
                {h.groups?.map((g) => (
                  <li key={g.shop_id}>
                    {g.shop_name}：
                    {g.dates.map((d, i) => (
                      <span key={d}>
                        {i > 0 && "、"}
                        <time dateTime={d}>{d}</time>
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p>查看其他期间的数据，请进入对应看板。</p>
          {row.id === "product_daily" && (
            <p>商品日经营只检查店铺日是否有记录，不推断每个商品每天都应有记录。</p>
          )}
          {h.checked_at && <p>最近完成覆盖检查：{localTime(h.checked_at)}</p>}
          {h.attempted_at && <p>最近尝试：{localTime(h.attempted_at)}</p>}
        </>
      )}
    </section>
  );
}
