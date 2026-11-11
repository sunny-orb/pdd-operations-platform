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

/** 上游时效只消费后端时间证据，不复算日期、不推导接收或经营完整性。 */
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
      <Tag color={delayed ? "warning" : "default"}>{labels[status.state]}</Tag>
    </span>
  );
}

export function UpdateTimeliness({ row }: { row: SourceStatus }) {
  const status = row.timeliness;
  if (!status || status.state === "not_applicable") return null;
  return (
    <section className="m6-update-timeliness" aria-label="上游更新时效依据">
      <strong>上游更新时效：{labels[status.state]}</strong>
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
