import { Button, Empty, Skeleton, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Action, Meta, Overview } from "./contracts";
import { Metrics } from "./Metrics";
import { formatRateChange, formatReportValue } from "./format";
import { Explanation } from "./Explanation";

const columns: ColumnsType<Action> = [
  {
    title: "状态",
    dataIndex: "status",
    width: 120,
    render: (status: string) => (
      <span className={`m5-decision-badge${status === "预警 P1" ? " is-risk" : ""}`}>
        {status || "待补充"}
      </span>
    ),
  },
  { title: "对象", dataIndex: "object", width: 130 },
  {
    title: "影响金额",
    width: 120,
    render: (_, row) => (
      <>
        {formatReportValue(row.amount, "currency")}
        <small>占实际入库金额 {formatReportValue(row.amount_share, "percent")}</small>
      </>
    ),
  },
  {
    title: "风险评分",
    width: 115,
    render: (_, row) => (
      <>
        <b>{formatReportValue(row.risk_score)}</b>
      </>
    ),
  },
  { title: "根因假设", dataIndex: "hypothesis", width: 205 },
  { title: "整改动作", dataIndex: "action", width: 205 },
  { title: "验证指标", dataIndex: "verification", width: 190 },
  {
    title: "上期 / 当前退款率",
    width: 145,
    render: (_, row) => (
      <>
        {formatReportValue(row.previous_rate, "percent")} /{" "}
        {formatReportValue(row.current_rate, "percent")}
      </>
    ),
  },
];

const stageNames = ["未发货退款", "已发货拒收", "签收后退货"];
const sizeSlots = [
  ["偏大", "尺码偏大"],
  ["偏小", "尺码偏小"],
  ["未说明", "尺码不合适未说明"],
] as const;

function riskEntries(items: Overview["category_risks"] = []) {
  return items
    .filter((item) => item.risk_score !== null)
    .slice(0, 5)
    .map((item) => ({
      id: item.id,
      title: `${item.name} · 排序分 ${formatReportValue(item.risk_score)}`,
      detail: `总退款率 ${formatReportValue(item.total_refund_rate, "percent")}，${formatRateChange(item.change_pp)}。实际入库金额占比 ${formatReportValue(item.amount_share, "percent")}。`,
    }));
}

export function Details({
  data,
  meta,
  actionsPending = false,
  actionsError = false,
  retryActions,
}: {
  data: Overview | undefined;
  meta?: Meta;
  actionsPending?: boolean;
  actionsError?: boolean;
  retryActions?: () => void;
}) {
  const sources = meta?.sources ?? [];
  const sourceNotes = [...new Set(sources.map((source) => source.note).filter(Boolean))];
  const riskMetrics = data?.risk_metrics;
  const rate = riskMetrics?.find((item) => item.key === "total_rate")?.value;
  const amount = riskMetrics?.find((item) => item.key === "total_amount")?.value;
  const change = riskMetrics?.find((item) => item.key === "change_pp")?.value;
  const target = data?.target_ranges.find((item) => item.metric_code === "total_refund_rate");
  const groups = [
    { label: "品类重点 Top 5", items: riskEntries(data?.category_risks), tone: "risk" },
    { label: "单款重点 Top 5", items: riskEntries(data?.style_risks), tone: "risk" },
    { label: "品类优化 Top 3", items: data?.category_actions.slice(0, 3), tone: "action" },
    { label: "单款优化 Top 3", items: data?.style_actions.slice(0, 3), tone: "action" },
  ];

  return (
    <div className="m5-details-body">
      <section className="m5-panel m5-risk-panel" id="aftersales-risk-overview">
        <div className="m5-panel-heading m5-bordered-heading">
          <div>
            <h2>退款风险与目标</h2>
          </div>
          <span>
            {target?.state === "scope_not_applicable"
              ? "目标不适用于当前筛选"
              : target
                ? `总退款率目标 ${formatReportValue(target.lower_rate, "percent")}–${formatReportValue(target.upper_rate, "percent")}`
                : "目标待核对"}
          </span>
        </div>
        <Metrics metrics={riskMetrics} variant="risk" />
      </section>
      <details className="m5-panel m5-reading" aria-label="数据与计算口径">
        <summary>数据与计算口径</summary>
        <p>{data?.scope_note || "等待报告数据。"}</p>
        <p>{data?.quality_note || "缺失的金额、数量与比率以「—」显示。"}</p>
        {meta && (
          <p>
            {meta.complete_through
              ? `完整性已确认至 ${meta.complete_through}`
              : "数据截止日来自已接收数据的实际覆盖范围，上游未提供完整性确认。"}
          </p>
        )}
        {sourceNotes.map((note) => (
          <p key={note}>
            {sourceNotes.length > 1 && (
              <b>
                {sources
                  .filter((source) => source.note === note)
                  .map((source) => source.label)
                  .join("、")}
                ：
              </b>
            )}
            {note}
          </p>
        ))}
        <p>近30天周报采用滚动30天，非自然周；月报按报告日期展示。各退款率独立计算，不相加。</p>
        <p>
          排序分只在品类或单款本层比较，不是风险概率或预警级别；预警按SOP标注。整改建议来自规则，不代表已执行；负责人和效果需实际记录。
        </p>
        {data?.decision.note && <p>{data.decision.note}</p>}
      </details>
      <section className="m5-panel m5-insights" aria-labelledby="aftersales-insights-title">
        <div className="m5-panel-heading m5-bordered-heading">
          <div>
            <h2 id="aftersales-insights-title">本期售后风险与优化建议</h2>
          </div>
        </div>
        <div className="m5-insight-content">
          <div className="m5-insight-trend">
            <strong>总退款率 {formatReportValue(rate, "percent")}</strong>
            <p>
              {formatRateChange(change)}，当前退款金额 {formatReportValue(amount, "currency")}
            </p>
          </div>
          <div className="m5-insight-grid">
            {groups.map(({ label, items, tone }) => (
              <article className={`m5-insight-${tone}`} key={label}>
                <h3>{label}</h3>
                {items?.length ? (
                  <ol>
                    {items.map((item, index) => (
                      <li key={item.id}>
                        <span className="m5-insight-rank" aria-hidden="true">
                          {index + 1}
                        </span>
                        <strong>{item.title}</strong>
                        <p>{item.detail}</p>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>{tone === "risk" ? "当前范围暂无重点排序项" : "当前范围暂无优化建议"}</p>
                )}
              </article>
            ))}
          </div>
        </div>
      </section>
      <section className="m5-panel m5-action-panel" aria-labelledby="aftersales-action-list">
        <div className="m5-panel-heading">
          <div>
            <h2 id="aftersales-action-list">整改行动清单</h2>
          </div>
          {!actionsPending && !actionsError && <span>{data?.actions.length ?? 0} 项</span>}
        </div>
        {actionsPending ? (
          <div role="status" aria-label="正在读取整改清单">
            <Skeleton active paragraph={{ rows: 3 }} />
          </div>
        ) : actionsError ? (
          <Button onClick={retryActions}>重试读取整改清单</Button>
        ) : (
          <Table
            className="m5-action-table"
            columns={columns}
            dataSource={data?.actions ?? []}
            rowKey="id"
            size="small"
            expandable={
              data?.actions.some((row) => row.note)
                ? {
                    columnTitle: "依据",
                    columnWidth: 48,
                    rowExpandable: (row) => !!row.note,
                    expandedRowRender: (row) => <p className="m5-row-notes">{row.note}</p>,
                  }
                : undefined
            }
            scroll={{ x: 1230, y: 360 }}
            pagination={{
              pageSize: 10,
              showSizeChanger: false,
              hideOnSinglePage: true,
            }}
            locale={{
              expand: "展开判断依据",
              collapse: "收起判断依据",
              emptyText: (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前范围暂无行动建议" />
              ),
            }}
          />
        )}
      </section>
      <section className="m5-stage-row">
        <div className="m5-panel">
          <div className="m5-panel-heading">
            <div>
              <h2>退款阶段结构</h2>
            </div>
          </div>
          <div className="m5-stage-grid">
            {stageNames.map((name) => {
              const stage = data?.stages.find((item) => item.name === name);
              return (
                <article key={name}>
                  <div className="m5-stage-caption">
                    <h3>{name}</h3>
                  </div>
                  <strong>
                    {stage?.amount == null
                      ? "待补数据"
                      : formatReportValue(stage.amount, "currency")}
                  </strong>
                  <p>
                    件数 {formatReportValue(stage?.qty)} · 金额占比{" "}
                    {formatReportValue(stage?.share, "percent")}
                  </p>
                </article>
              );
            })}
          </div>
          {data?.stages.length ? (
            <Explanation label="阶段口径">
              <dl>
                {data.stages.map((stage) => (
                  <div key={stage.name}>
                    <dt>{stage.name}</dt>
                    <dd>
                      <p>{stage.note}</p>
                      <p>{stage.trend}</p>
                    </dd>
                  </div>
                ))}
              </dl>
            </Explanation>
          ) : null}
        </div>
        <div className="m5-panel">
          <div className="m5-panel-heading">
            <div>
              <h2>尺码问题拆分</h2>
            </div>
          </div>
          <div className="m5-size-grid">
            {sizeSlots.map(([label, name]) => {
              const item = data?.size_breakdown.find((issue) => issue.name === name);
              return (
                <article key={name}>
                  <span>{label}</span>
                  <strong>{formatReportValue(item?.qty)}</strong>
                  <small>占实退数量 {formatReportValue(item?.share, "percent")}</small>
                </article>
              );
            })}
          </div>
        </div>
      </section>
      <Metrics metrics={data?.metrics} variant="summary" />
    </div>
  );
}
