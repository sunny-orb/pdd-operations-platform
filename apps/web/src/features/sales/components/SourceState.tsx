import { memo } from "react";
import { localTime } from "../format";
type State = {
  state: string;
  last_attempt_status?: string | null;
  last_attempt_at?: string | null;
  last_success_at?: string | null;
};
/** 旧成功值继续可读时，逐来源失败与成功时间保持可见，不能用店铺日状态代替。 */
export const SourceState = memo(function SourceState({
  source,
  label,
}: {
  source?: State | null;
  label: string;
}) {
  const status = source?.last_attempt_status;
  const failed = status === "failed" || status === "abandoned";
  const text = failed
    ? source?.last_success_at
      ? "本次同步失败，保留上次成功值"
      : "本次同步失败，尚无成功数据"
    : status === "running"
      ? "同步进行中"
      : source?.state === "observed"
        ? "已发布本地数据"
        : "来源未接入";
  return (
    <span className={failed ? "source-state source-state-failed" : "source-state"}>
      {label}：{text} · 最近成功 {localTime(source?.last_success_at)} · 最近尝试{" "}
      {source?.last_attempt_at ? localTime(source.last_attempt_at) : "—"}
    </span>
  );
});
