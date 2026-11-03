import { memo } from "react";
import { HistoricalSupplements } from "../../shared/promotion/HistoricalSupplements";
import { Tag } from "antd";
import type { z } from "zod";
import { metadata } from "./contracts";
import { localTime } from "./format";
const text = (value: unknown, fallback = "未知") =>
  typeof value === "string" && value ? value : fallback;
export const SourceState = memo(function SourceState({
  data,
  version,
  supplements,
}: {
  data: z.infer<typeof metadata> | undefined;
  version: string;
  supplements?: z.infer<typeof metadata>["historical_supplements"];
}) {
  return (
    <details className="m2-source-state">
      <summary>
        来源与更新状态 · 完整截至日 {data?.complete_through ?? "未知"} · 当前仅报告实际观察范围
      </summary>
      <ul>
        {data?.sources.map((source, index) => {
          const attempt =
            source.latest_attempt && typeof source.latest_attempt === "object"
              ? (source.latest_attempt as Record<string, unknown>)
              : {};
          const failed = attempt.status === "failed";
          return (
            <li key={text(source.dataset, String(index))}>
              <b>{text(source.table, text(source.dataset, "来源未接通"))}</b>
              <Tag
                color={failed ? "error" : source.status === "not_connected" ? "default" : "blue"}
              >
                {failed
                  ? "最近接收失败，保留成功版本"
                  : source.status === "not_connected"
                    ? "未接通"
                    : "已观察，完整性未知"}
              </Tag>
              <br />
              {source.date_status === "not_applicable"
                ? "资料快照，不适用业务日期"
                : `观察范围 ${text(source.observed_from, "—")} — ${text(source.observed_through, "—")}`}
              ；完整截至日 {text(source.complete_through)}；最近成功{" "}
              {localTime(source.last_success_at)}；最近尝试{" "}
              {localTime(attempt.finished_at ?? attempt.started_at)}（
              {text(attempt.status, "尚无记录")}）
              {attempt.error_code ? `；失败原因 ${text(attempt.error_code)}` : ""}
              <br />
              来源版本 {text(source.version, "—")}
            </li>
          );
        })}
      </ul>
      {!data?.sources.length && <p>暂无可用来源元信息</p>}
      {data?.mode === "store" &&
        (supplements ? (
          <HistoricalSupplements rows={supplements} />
        ) : (
          <p>当前筛选尚无可用补齐状态，请检查日期范围或等待加载。</p>
        ))}
      <small>
        查询版本 {version || "尚未接收"}
        ；页面刷新仅重读本地已发布结果。各来源分别报告观察日，不将最慢来源的截止日当作全页截止。
      </small>
    </details>
  );
});
