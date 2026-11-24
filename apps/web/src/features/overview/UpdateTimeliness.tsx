import { Tag } from "antd";
import type { SourceStatus } from "./sourceStatusApi";
import { localTime } from "./model";
import { sourceIsDelayed } from "./dataUpdateAlerts";

const labels = {
  updated: "今日已更新",
  waiting: "未到判断期限",
  awaiting_check: "等待核对",
  delayed: "更新延迟",
  unknown: "暂无可靠时间依据",
  not_applicable: "不适用",
};

function statusLabel(status: NonNullable<SourceStatus["timeliness"]>) {
  if (status.basis === "upstream_business_day") {
    if (status.state === "updated") return "已见昨日数据";
    if (status.state === "unknown") return "缺少昨日见数时间";
  }
  return labels[status.state];
}
export function UpdateTimelinessLabel({ row }: { row: SourceStatus }) {
  const status = row.timeliness;
  if (!status || status.state === "not_applicable") return null;
  const delayed = sourceIsDelayed(row);
  return (
    <span className="m6-update-status-label">
      {delayed && (
        <span
          className="m6-update-row-dot m6-update-delay-dot"
          role="img"
          aria-label={`更新延迟：${row.name}`}
          title="更新延迟，展开明细查看时间依据"
        />
      )}
      <Tag color={delayed ? "warning" : "default"}>{statusLabel(status)}</Tag>
    </span>
  );
}

export function UpdateTimeliness({ row }: { row: SourceStatus }) {
  const status = row.timeliness;
  if (!status || status.state === "not_applicable") return null;
  if (status.basis === "upstream_business_day") {
    return (
      <section className="m6-update-timeliness" aria-label="上游日报时效依据">
        <strong>上游数据时效：{statusLabel(status)}</strong>
        {status.required_date && <p>应到业务日：{status.required_date}（北京时间昨天）。</p>}
        {status.baseline_first_seen_at && (
          <p>昨日首次见数：{localTime(status.baseline_first_seen_at)}。</p>
        )}
        {status.expected_by && (
          <p>今日判断期限：{localTime(status.expected_by)}（昨日首次见数的钟点加一小时）。</p>
        )}
        {status.first_seen_at && <p>今日首次见数：{localTime(status.first_seen_at)}。</p>}
        {status.observed_at && <p>实际核对：{localTime(status.observed_at)}。</p>}
        {status.state === "delayed" && <p>超过判断期限后核对，上游尚无该业务日的记录。</p>}
        {status.state === "awaiting_check" && <p>已过判断期限，等待期限后的上游核对结果。</p>}
        {(status.state === "unknown" ||
          (status.state === "updated" && !status.baseline_first_seen_at)) && (
          <p>尚无昨日首次见到前一业务日记录的时间，暂无法比较到数早晚。</p>
        )}
        {status.arrival_timing === "confirmed_late" && (
          <p>
            已确认延迟，现已到数。
            {status.last_absent_at && `截至 ${localTime(status.last_absent_at)} 仍未见数。`}
          </p>
        )}
        {status.arrival_timing === "first_seen_after_deadline" && (
          <p>首次见数晚于期限。后台按时点核对，实际到数可能更早。</p>
        )}
        {status.arrival_timing === "within_deadline" && <p>判断期限内已见昨日数据。</p>}
      </section>
    );
  }
  return (
    <section className="m6-update-timeliness" aria-label="上游更新时效依据">
      <strong>上游更新时效：{statusLabel(status)}</strong>
      {status.confirmed_updated_at && <p>已核实的今日更新：{status.confirmed_updated_at}。</p>}
      {status.baseline_updated_at && (
        <p>
          昨日上游标注：{status.baseline_updated_at}；今日判断期限：{status.expected_by}。
        </p>
      )}
      {status.observed_at && <p>网页实际核对：{localTime(status.observed_at)}。</p>}
      <p>
        {status.state === "delayed"
          ? "截至上述核对，超过判断期限仍未观察到今日更新。请由平台维护人员复核；确认上游未更新后，由对应来源维护人员处理或补跑上游任务，成功后平台按既有计划接收。维护联系人未登记。已有成功数据继续展示。"
          : status.state === "awaiting_check"
            ? "已过判断期限，但缺少期限后的成功核对，暂不判延迟；按既有计划再次核对。"
            : status.state === "unknown"
              ? "缺少可核实的昨日更新时间或当前核对证据，不判断延迟；不以业务日期、无记录或同步时间代替。"
              : status.state === "updated"
                ? "已观察到今天的上游更新；平台是否接收成功见同步状态，记录情况单独说明。"
                : "尚未超过昨天更新时间推到今天再加一小时的期限，继续按原计划核对。"}
      </p>
    </section>
  );
}
