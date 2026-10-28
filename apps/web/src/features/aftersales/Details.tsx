import { Empty, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Action, Overview } from "./contracts";
import { Metrics } from "./Metrics";
import { formatValue } from "./format";
const columns: ColumnsType<Action> = [
  { title: "状态", dataIndex: "status", width: 120, render: (value) => <Tag>{value}</Tag> },
  { title: "对象", dataIndex: "object", width: 180 },
  {
    title: "影响金额",
    width: 165,
    render: (_, row) => (
      <>
        {formatValue(row.amount, "currency")}
        <small>实际入库金额占比 {formatValue(row.amount_share, "percent")}</small>
      </>
    ),
  },
  {
    title: "风险评分",
    width: 125,
    render: (_, row) => (
      <>
        {formatValue(row.risk_score)}
        <small>{row.note}</small>
      </>
    ),
  },
  { title: "根因假设", dataIndex: "hypothesis", width: 230 },
  { title: "整改动作", dataIndex: "action", width: 260 },
  { title: "验证指标", dataIndex: "verification", width: 230 },
  {
    title: "上期 / 当前退款率",
    width: 195,
    render: (_, row) => (
      <>
        {formatValue(row.previous_rate, "percent")} / {formatValue(row.current_rate, "percent")}
        <small>上期报告对比，非实际整改效果</small>
      </>
    ),
  },
];
export function Details({ data }: { data: Overview | undefined }) {
  return (
    <div className="m5-details-body">
      <section className="m5-panel">
        <div className="m5-panel-heading">
          <div>
            <span className="m5-eyebrow">风险总览</span>
            <h2>退款风险、目标差距与数据状态</h2>
          </div>
        </div>
        <Metrics metrics={data?.risk_metrics ?? []} label="风险五项指标" />
      </section>
      <section className="m5-panel m5-reading">
        <h3>管理层阅读口径</h3>
        <p>{data?.scope_note ?? "等待真实报告"}</p>
        <p>
          当前接入聚水潭售后与销售明细，行数不是去重订单数。实退数量仅按已核必要样例范围观察；实际入库金额仍受来源口径门禁限制，未通过时不计算金额及三率。售后确认时间与销售支付时间分别组织期间。
        </p>
        {data?.quality_note && <p>{data.quality_note}</p>}
      </section>
      <section className="m5-panel">
        <div className="m5-panel-heading">
          <div>
            <span className="m5-eyebrow">核心结论</span>
            <h2>本期售后风险与优化建议</h2>
          </div>
        </div>
        <div className="m5-insight-grid">
          {data?.core_insights.map((item, index) => (
            <article key={`${item.label}-${index}`}>
              <span>{item.label}</span>
              <h3>{item.title}</h3>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
        <div className="m5-insight-grid">
          {(
            [
              { label: "品类风险 Top 5", items: data?.category_risks },
              { label: "单款风险 Top 5", items: data?.style_risks },
            ] as const
          ).map((group) => (
            <article key={group.label}>
              <h3>{group.label}</h3>
              {group.items?.length ? (
                <ol>
                  {group.items.slice(0, 5).map((item) => (
                    <li key={item.id}>
                      <strong>{item.name}</strong>
                      <p>
                        总退款率 {formatValue(item.total_refund_rate, "percent")} · 风险分{" "}
                        {formatValue(item.risk_score)}
                      </p>
                      <small>
                        实际入库金额 {formatValue(item.amount, "currency")} · 占比{" "}
                        {formatValue(item.amount_share, "percent")}；{item.note}
                      </small>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>缺少可比较的输入，暂不排序。</p>
              )}
            </article>
          ))}
        </div>
        <div className="m5-insight-grid">
          {(
            [
              { label: "品类优化 Top 3", items: data?.category_actions },
              { label: "单款优化 Top 3", items: data?.style_actions },
            ] as const
          ).map((group) => (
            <article key={group.label}>
              <h3>{group.label}</h3>
              {group.items?.length ? (
                <ol>
                  {group.items.slice(0, 3).map((item) => (
                    <li key={item.id}>
                      <strong>{item.title}</strong>
                      <p>{item.detail}</p>
                      <small>{item.verification}</small>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>暂无可核查建议。</p>
              )}
            </article>
          ))}
        </div>
      </section>
      <section className="m5-panel">
        <div className="m5-panel-heading">
          <div>
            <h2>整改行动清单</h2>
            <p>分析建议；不代表已认领、已执行或已关闭的人工工单</p>
          </div>
          <span>{data?.actions.length ?? 0} 项</span>
        </div>
        <Table
          columns={columns}
          dataSource={data?.actions ?? []}
          rowKey="id"
          size="small"
          scroll={{ x: 1530 }}
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
            {data?.stages.map((stage) => (
              <article key={stage.name}>
                <h3>{stage.name}</h3>
                <Tag>{stage.status}</Tag>
                <strong>{formatValue(stage.amount, "currency")}</strong>
                <p>
                  件数 {formatValue(stage.qty)} · 金额占比 {formatValue(stage.share, "percent")}
                </p>
                <small>
                  {stage.note}；{stage.trend}
                </small>
              </article>
            ))}
          </div>
        </div>
        <div className="m5-panel">
          <h2>尺码问题拆分</h2>
          <p>偏大、偏小、未说明分开统计；占比以尺码问题为分母</p>
          <div className="m5-size-grid">
            {data?.size_breakdown.map((item) => (
              <article key={item.name}>
                <span>{item.name}</span>
                <strong>{formatValue(item.qty)}</strong>
                <small>{formatValue(item.share, "percent")}</small>
              </article>
            ))}
          </div>
        </div>
      </section>
      <Metrics metrics={data?.metrics ?? []} label="售后九项指标" />
    </div>
  );
}
