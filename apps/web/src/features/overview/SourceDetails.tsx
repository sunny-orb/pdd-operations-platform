import { memo } from "react";
import type { DomainState } from "./data";
import { domains, localTime, type ObservationCoverage } from "./model";
import { Button } from "antd";
import { ReloadOutlined } from "@ant-design/icons";

const coverageLabels: Record<ObservationCoverage["status"], string> = {
  observed: "观察列表已读取",
  retained: "查询失败，保留旧观察",
  failed: "查询失败，未纳入",
  loading: "读取中",
  not_connected: "观察来源未接收",
  not_implemented: "未接异常接口",
  pending_validation: "异常证据待核，空清单不计作零",
};

/** 二级展开保留真实来源、覆盖和运行记录；刷新仅调用原有本地查询。 */
export const SourceDetails = memo(function SourceDetails({
  states,
  coverage,
}: {
  states: readonly DomainState[];
  coverage: readonly ObservationCoverage[];
}) {
  return (
    <>
      <div className="m6-source-tools">
        <p>页面每45秒或切回窗口检查本地版本，手动刷新不触发上游同步。</p>
        <Button
          aria-label="刷新本地查询"
          icon={<ReloadOutlined />}
          loading={states.some((state) => state.refreshing)}
          onClick={() => states.forEach((state) => state.refresh())}
        >
          刷新本地查询
        </Button>
      </div>
      <div className="m6-source-grid">
        {states.map((state) => {
          const domain = domains.find((item) => item.id === state.id)!;
          const observation =
            coverage.find((item) => item.domain === state.id)?.status ?? "not_connected";
          const notes = [
            ...new Set([
              ...(state.data?.card.pairs
                .filter((field) => field.note)
                .map((field) => `${field.label}：${field.note}`) ?? []),
              ...(state.meta?.notes ?? []),
            ]),
          ].filter((note) => note !== state.data?.card.note);
          return (
            <article className="m6-source-details" key={state.id}>
              <header>
                <h4>{state.id === "sales" ? "店铺销售" : domain.label}</h4>
                <span>{coverageLabels[observation]}</span>
              </header>
              <p>{state.data?.card.note ?? "尚未取得来源说明"}</p>
              {state.error && <p className="m6-source-error">{state.error}</p>}
              <dl>
                <div>
                  <dt>观察截至</dt>
                  <dd>{state.meta?.date ?? "未知"}</dd>
                </div>
                <div>
                  <dt>完整截至</dt>
                  <dd>{state.meta?.completeThrough ?? "未确认"}</dd>
                </div>
                <div>
                  <dt>最近成功</dt>
                  <dd>{localTime(state.meta?.success)}</dd>
                </div>
                <div>
                  <dt>最近尝试</dt>
                  <dd>
                    {localTime(state.meta?.attempt)} · {state.meta?.attemptStatus ?? "尚无状态"}
                  </dd>
                </div>
                <div>
                  <dt>来源状态</dt>
                  <dd>{state.meta?.status ?? "未知"}</dd>
                </div>
              </dl>
              {notes.length > 0 && (
                <ul>
                  {notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              )}
            </article>
          );
        })}
      </div>
    </>
  );
});
