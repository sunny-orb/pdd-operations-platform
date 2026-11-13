import { Empty, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Action, Overview } from "./contracts";
import { Metrics } from "./Metrics";
import { formatRateChange, formatReportValue } from "./format";

const columns: ColumnsType<Action> = [
  {
    title: "状态",
    dataIndex: "status",
    width: 120,
    render: (status: string) => (
      <span className={`m5-decision-badge${status === "待整改" ? " is-risk" : ""}`}>
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
        {row.note && <small>{row.note}</small>}
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
      title: `${item.name} · 风险 ${formatReportValue(item.risk_score)}`,
      detail: `总退款率 ${formatReportValue(item.total_refund_rate, "percent")}，${formatRateChange(item.change_pp)}。实际入库金额占比 ${formatReportValue(item.amount_share, "percent")}。`,
    }));
}

export function Details({ data }: { data: Overview | undefined }) {
  const riskMetrics = data?.risk_metrics;
  const rate = riskMetrics?.find((item) => item.key === "total_rate")?.value;
  const amount = riskMetrics?.find((item) => item.key === "total_amount")?.value;
  const change = riskMetrics?.find((item) => item.key === "change_pp")?.value;
  const target = data?.target_ranges.find((item) => item.metric_code === "total_refund_rate");
  const groups = [
    { label: "异常品类 Top 5", items: riskEntries(data?.category_risks), tone: "risk" },
    { label: "异常单款 Top 5", items: riskEntries(data?.style_risks), tone: "risk" },
    { label: "品类优化 Top 3", items: data?.category_actions.slice(0, 3), tone: "action" },
    { label: "单款优化 Top 3", items: data?.style_actions.slice(0, 3), tone: "action" },
  ];

  return (
    <div className="m5-details-body">
      <section className="m5-panel m5-risk-panel" id="aftersales-risk-overview">
        <div className="m5-panel-heading m5-bordered-heading">
          <div>
            <span className="m5-eyebrow">风险总览</span>
            <h2>退款风险、目标差距与数据状态</h2>
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
      <section className="m5-panel m5-reading" aria-labelledby="aftersales-reading-title">
        <h3 id="aftersales-reading-title">管理层阅读口径</h3>
        <p>{data?.scope_note || "等待报告数据。"}</p>
        <p>{data?.quality_note || "缺失的金额、数量与比率以「—」显示。"}</p>
      </section>
      <section className="m5-panel m5-insights" aria-labelledby="aftersales-insights-title">
        <div className="m5-panel-heading m5-bordered-heading">
          <div>
            <span className="m5-eyebrow">核心结论</span>
            <h2 id="aftersales-insights-title">本期售后风险与优化建议</h2>
          </div>
          <span>按当前筛选结果展示</span>
        </div>
        <div className="m5-insight-content">
          <div className="m5-insight-trend">
            <span>总体趋势</span>
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
                  <p>{tone === "risk" ? "暂无可核查风险评分" : "暂无可核查优化建议"}</p>
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
            <p>按风险评分展示对象、根因假设、整改动作和验证指标</p>
          </div>
          <span>{data?.actions.length ?? 0} 项</span>
        </div>
        <Table
          className="m5-action-table"
          columns={columns}
          dataSource={data?.actions ?? []}
          rowKey="id"
          size="small"
          scroll={{ x: 1230, y: 360 }}
          pagination={false}
          locale={{
            emptyText: (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无相容输入下的行动建议" />
            ),
          }}
        />
      </section>
      <section className="m5-stage-row">
        <div className="m5-panel">
          <div className="m5-panel-heading">
            <div>
              <h2>退款阶段结构</h2>
              <p>金额、件数、占比与趋势按可追溯数据展示</p>
            </div>
          </div>
          <div className="m5-stage-grid">
            {stageNames.map((name) => {
              const stage = data?.stages.find((item) => item.name === name);
              const traceable = stage?.status === "observed_reference";
              return (
                <article key={name}>
                  <div className="m5-stage-caption">
                    <h3>{name}</h3>
                    <span className={`m5-decision-badge${traceable ? " is-traceable" : ""}`}>
                      {traceable ? "可追踪" : "待验证"}
                    </span>
                  </div>
                  <strong>
                    {stage?.amount == null
                      ? "待补字段"
                      : formatReportValue(stage.amount, "currency")}
                  </strong>
                  <p>
                    件数 {formatReportValue(stage?.qty)} · 金额占比{" "}
                    {formatReportValue(stage?.share, "percent")}
                  </p>
                  {stage && (
                    <small>
                      {stage.note} {stage.trend}
                    </small>
                  )}
                </article>
              );
            })}
          </div>
        </div>
        <div className="m5-panel">
          <div className="m5-panel-heading">
            <div>
              <h2>尺码问题拆分</h2>
              <p>偏大、偏小、未说明分开统计</p>
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
