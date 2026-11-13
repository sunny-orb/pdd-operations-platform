import { Tag } from "antd";
import type { Meta } from "./contracts";
import { localTime } from "./format";
const statusLabels: Record<string, string> = {
  ready: "已接入",
  source_unavailable: "来源不可用",
  failed_retained: "最近接收失败，保留成功版本",
  no_records: "本报告无记录",
  observed: "已接入，口径待核",
  new_input_pending_report: "新输入已接收，报告待重算",
};
export function SourceState({
  meta,
  reportGeneratedAt,
}: {
  meta: Meta | undefined;
  reportGeneratedAt?: string | null;
}) {
  return (
    <details className="m5-source-state">
      <summary>
        来源与更新状态 · 观察截至 {meta?.observed_to ?? "未取得"} · 完整截至日{" "}
        {meta?.complete_through ?? "未知"}
      </summary>
      <p>
        最近成功：{localTime(meta?.last_success_at)}；最近尝试：{localTime(meta?.last_attempt_at)}
        。页面刷新只读本地已发布结果。
      </p>
      {reportGeneratedAt && <p>报告生成：{localTime(reportGeneratedAt)}</p>}
      <ul>
        {meta?.sources.map((source) => (
          <li key={source.code}>
            <strong>{source.label}</strong>{" "}
            <Tag color={source.status === "ready" ? "green" : "gold"}>
              {statusLabels[source.status] ?? "待核验"}
            </Tag>
            <span>{source.locator ?? "物理来源尚未核准"}</span>
            <p>
              观察范围 {source.observed_from ?? "未知"} — {source.observed_to ?? "未知"}；成功{" "}
              {localTime(source.last_success_at)}；尝试 {localTime(source.last_attempt_at)}
              {source.error_code ? `；原因 ${source.error_code}` : ""}
            </p>
            <small>{source.note}</small>
          </li>
        ))}
      </ul>
      {!meta?.sources.length && <p>等待来源状态，未以样例或历史数字补充。</p>}
    </details>
  );
}
