import { useEffect } from "react";
import { Alert, Button, Spin } from "antd";
import { diagnosis, type DiagnosisData, type DiagnosisObject, type Metric } from "./contracts";
import { useResource, type PromotionState } from "./data";
import { changeValue, formatClue, formatMetric, formatValue } from "./format";

const comparison = (current: Metric | undefined, previous: Metric | undefined) =>
  `${formatMetric(previous)} → ${formatMetric(current)}（${changeValue(current?.change)}）`;
function ObjectTable({ items }: { items: DiagnosisObject[] }) {
  return (
    <div className="sop-table">
      <table>
        <thead>
          <tr>
            <th>对象 / 当前花费</th>
            <th>证据与规则</th>
            <th>核查及行动建议</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.object_key}>
              <td>
                <b>{item.label}</b>
                <small>
                  {item.shop_name ?? "店铺资料缺失"}
                  {item.focus ? ` · 重点款 ${item.level ?? "等级缺失"}` : ""}
                </small>
                <span>
                  {item.current_record_count === "0"
                    ? "本期无记录"
                    : formatMetric(item.current.spend)}{" "}
                  · {item.priority}
                </span>
                <small>资料运营：{item.operator ?? "未分配"}</small>
              </td>
              <td>
                <p>实际投产 {comparison(item.current.roi, item.previous.roi)}</p>
                <p>净投产 {comparison(item.current.net_roi, item.previous.net_roi)}</p>
                {item.findings.map((finding) => (
                  <p key={finding.rule}>
                    <b>
                      {finding.level} · {finding.rule}
                    </b>
                    <br />
                    {finding.evidence}
                  </p>
                ))}
                {!item.findings.length && <p>当前未形成可评级的推广异常结论；不代表利润达标。</p>}
                <small>
                  本期 / 基期有记录日期：{item.current_days} / {item.previous_days} 天；缺行不补零。
                </small>
                {item.notes.map((note) => (
                  <small key={note}>{note}</small>
                ))}
              </td>
              <td>
                {item.findings.map((finding) => (
                  <p key={finding.rule}>{finding.action}</p>
                ))}
                {item.clues.map((clue) => (
                  <p key={clue.label}>
                    <b>{clue.label}线索：</b>
                    {formatClue(clue)}；{clue.action}
                  </p>
                ))}
                {!item.findings.length && !item.clues.length && (
                  <p>持续跟踪投产与成交承接，核对商品利润底线、退款及库存后再评估预算。</p>
                )}
                {item.focus && /新品/.test(item.level ?? "") && (
                  <p>新品结合测试阶段评估，不直接停投。</p>
                )}
                <small>
                  原因未确认 · 建议待核查
                  <br />
                  目标：完成原因核对后制定；截止 / 复盘时间待安排。
                </small>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <p className="m2-empty">此范围暂无对应对象。</p>}
    </div>
  );
}
export function Diagnosis({ state }: { state: PromotionState }) {
  const query = useResource(
    "diagnosis",
    diagnosis,
    "product",
    state.scope,
    state.version,
    state.invalid,
  );
  useEffect(() => {
    if (query.error) state.stale(query.error);
  });
  return (
    <DiagnosisView
      data={query.data?.data}
      pending={query.isPending && !!state.version && !state.invalid}
      error={query.error}
      retry={() => void query.refetch()}
    />
  );
}
export function DiagnosisView({
  data,
  pending = false,
  error,
  retry,
}: {
  data: DiagnosisData | undefined;
  pending?: boolean;
  error?: Error | null;
  retry?: () => void;
}) {
  const overall = data?.overall;
  const counts = data?.counts;
  return (
    <section className="panel sop-diagnosis" aria-label="SOP经营诊断" id="promotion-diagnosis">
      <div className="panel-title">
        <div>
          <span>AI 经营诊断 · SOP 规则分析</span>
          <h2>从推广结果定位到商品与行动</h2>
        </div>
        <a
          href="https://kocotree.feishu.cn/docx/EA2fdkbbLoMhYnxNEEvcxFhPnoc"
          target="_blank"
          rel="noreferrer"
        >
          分析 SOP v1.4 ↗
        </a>
      </div>
      <p className="sop-meta">
        诊断：{data?.current_window.start ?? "—"} — {data?.current_window.end ?? "—"}；对比：
        {data?.previous_window.start ?? "—"} — {data?.previous_window.end ?? "—"}
        。沿用页面的店铺、商品、品类、等级和运营筛选；以所选结束日为锚点，固定比较两个 7
        天日历周期。
      </p>
      {pending && <Spin size="small" />}
      {error && (
        <Alert
          type="error"
          title="SOP诊断查询未完成"
          description={error.message}
          action={<Button onClick={retry}>重试诊断</Button>}
        />
      )}
      {(!data || data.status !== "ready") && (
        <p className="sop-notice">
          {data?.coverage.missing_count
            ? `当前对象缺少 ${data.coverage.missing_count} 个日记录。`
            : "来源完整性尚未得到证明。"}
          以下金额与比率保留已记录数据参考，暂停趋势评级；先核对覆盖再判断周期变化。
        </p>
      )}
      {data?.coverage.stale && (
        <p className="sop-notice">
          源数据观察截至 {data.coverage.observed_through ?? "未知"}
          ，尚未更新至昨日。本模块保留历史数据复核，暂停趋势评级，暂不生成新的经营判断。
        </p>
      )}
      <div className="sop-summary">
        <article>
          <span>经营结果</span>
          <h3>
            {overall && overall.current_record_count !== "0"
              ? `实际投产 ${formatMetric(overall.current.roi)}`
              : "本期无可用推广结论"}
          </h3>
          <p>
            花费 {formatMetric(overall?.current.spend)}；交易额{" "}
            {formatMetric(overall?.current.revenue)}。
          </p>
          <p>
            实际投产 {comparison(overall?.current.roi, overall?.previous.roi)}；净投产{" "}
            {comparison(overall?.current.net_roi, overall?.previous.net_roi)}。
          </p>
          <p>
            CPC {formatMetric(overall?.current.cpc)} · CTR {formatMetric(overall?.current.ctr)} ·
            点击转化 {formatMetric(overall?.current.cvr)}。
          </p>
        </article>
        <article>
          <span>问题定位</span>
          <h3>
            {data?.status === "ready"
              ? `${counts?.flagged ?? 0} 个商品需复核`
              : "数据待核对，暂不评级"}
          </h3>
          <p>
            其中 P1 {counts?.p1 ?? "—"} 个，P2 {counts?.p2 ?? "—"} 个，待核实{" "}
            {counts?.observation ?? "—"} 个。按店铺＋商品 ID 分开分析。
          </p>
          <p>实际与净投产分别判断；两期花费均≥¥300，下降15%为关注、25%为预警。</p>
          {overall?.findings.slice(0, 3).map((finding) => (
            <p key={finding.rule}>
              <b>汇总 {finding.level}：</b>
              {finding.rule}，{finding.evidence}。
            </p>
          ))}
        </article>
        <article>
          <span>优先动作与机会</span>
          <h3>先核查原因，再决定预算</h3>
          <p>
            {data?.flagged_products[0]
              ? `优先复核 ${data.flagged_products[0].label}（花费 ${formatMetric(data.flagged_products[0].current.spend)}），同级按花费排序；花费不是损失金额。`
              : "暂无可确认的商品预警，继续检查重点款的成本、转化和退款变化。"}
          </p>
          <p>
            未接入同规格利润参数、库存与活动状态，暂不判定利润达标或建议放量。所有行动为待核查建议。
          </p>
        </article>
      </div>
      {!!overall?.notes.length && <p className="sop-notice">{overall.notes.join(" ")}</p>}
      {!!overall?.clues.length && (
        <div className="sop-clues">
          <h3>汇总变化线索 · 原因待验证</h3>
          {overall.clues.map((clue) => (
            <p key={clue.label}>
              <b>{clue.label}：</b>
              {formatClue(clue)}。{clue.action}
            </p>
          ))}
          <small>CTR、CPC、点击转化暂无已确认独立预警阈值，变化不直接证明原因。</small>
        </div>
      )}
      <details open>
        <summary>商品异常与行动清单（{counts?.flagged ?? "—"}）</summary>
        <ObjectTable items={data?.flagged_products ?? []} />
      </details>
      <details>
        <summary>重点款完整诊断：S、A、专供、新品（{counts?.focused ?? "—"}）</summary>
        <ObjectTable items={data?.focused_products ?? []} />
      </details>
      <details>
        <summary>
          按店铺 / 品类下钻（{counts?.shops ?? "—"} 家店铺、{counts?.categories ?? "—"}{" "}
          个店铺品类组合）
        </summary>
        <h3>店铺</h3>
        <ObjectTable items={data?.shops ?? []} />
        <h3>店铺内品类</h3>
        <ObjectTable items={data?.categories ?? []} />
      </details>
      <details>
        <summary>全部商品扫描（{counts?.products ?? "—"}）与数据说明</summary>
        <ObjectTable items={data?.products ?? []} />
        <p>
          数仓数据观察截至 {data?.coverage.observed_through ?? "未知"}
          ；独立同步状态见页头来源。两个周期有记录日期 {data?.coverage.covered_days ??
            "—"}/14；本期 {formatValue(String(counts?.current_records ?? ""))}{" "}
          条汇总记录；有评级限制的商品 {counts?.limited ?? "—"} 个（具体原因见各行）。
        </p>
        <p>
          本期资料未完全匹配 {counts?.unmatched_records ?? "—"} 条；缺商品 ID、跳过单款关联{" "}
          {counts?.missing_product_ids ?? "—"} 条。未匹配记录仍计入推广汇总，不能补齐等级或负责人。
        </p>
        <p>
          规则版本：{data?.rule_version ?? "SOP v1.4"}
          。利润四档、退款归因、库存、价格活动和素材需进一步关联验证。净交易额真实为零时保留零投产；平台净投产不与模型净投产直接比较。花费增长组合的独立最低样本门槛待确认。
        </p>
        <p>
          同比保留在趋势图查看，本模块不作同比评级。各层级金额存在包含关系，不累计相加。行动状态、目标与复盘记录未实际登记前显示待核查
          / 待安排。
        </p>
      </details>
    </section>
  );
}
