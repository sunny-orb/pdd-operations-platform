import { Collapse } from "antd";
import { Link } from "react-router-dom";
import type { DomainState } from "./data";
import { domains, type centerCounts, type DomainId, type ObservationCoverage } from "./model";
import { SourceDetails } from "./SourceDetails";

const rules: Record<DomainId, string> = {
  sales: "经营月累计 GMV；月目标来自飞书原版目标。缺目标时不计算达成率。",
  products: "最新观察单日 GMV，日环比与同比沿商品域返回值；风险另有观察窗。",
  promotion:
    "ROI（倍）=归因交易额÷花费；CTR=点击÷曝光，CVR=成交笔数÷点击。分子、分母各自汇总已知值，原始空值不补0，分母≤0不计算；缺项可算值仅作参考。诊断另有窗口。",
  aftersales: "按固定报告期间展示，金额和退款率沿售后核验门禁。整体、品类和单款金额不重复加总。",
};

const disclosureIcon = ({ isActive }: { isActive?: boolean }) => (
  <i
    className={isActive ? "m6-definitions-chevron is-open" : "m6-definitions-chevron"}
    aria-hidden="true"
  />
);

/** 口径摘要直接展示；覆盖统计沿完整结果，来源详情保持默认折叠。 */
export function DefinitionsSection({
  states,
  counts,
  coverage,
  sourceActionRows,
  unmappedActions,
}: {
  states: readonly DomainState[];
  counts: ReturnType<typeof centerCounts>;
  coverage: readonly ObservationCoverage[];
  sourceActionRows: number;
  unmappedActions: number;
}) {
  const observed = counts.availableDomains > 0 || counts.observations > 0;
  const actionsAvailable = counts.availableDomains > 0 || counts.actionRows > 0;
  return (
    <section id="definitions" className="m6-definitions">
      <article className="m6-definitions-panel">
        <header className="m6-definitions-header">
          <p className="m6-eyebrow">DATA GOVERNANCE</p>
          <h2>统一筛选与指标口径</h2>
        </header>
        <div className="m6-definitions-body">
          <div className="m6-definition-summary">
            <article>
              <h3>时间与范围</h3>
              <p>
                本页没有统一日期或店铺筛选，以各卡统计期间为准；观察日不等于完整日，天气按自己的发布日查询。
              </p>
              <p>
                有记录的零值保留，—
                表示缺失或不可用。金额和百分比按展示精度舍入；中心不跨域合计金额或再次扣退款。
              </p>
            </article>
            <article className="m6-definition-rules">
              <h3>各域口径</h3>
              <dl>
                {domains.map((domain) => {
                  const state = states.find((item) => item.id === domain.id);
                  return (
                    <div key={domain.id}>
                      <dt>
                        <Link to={state?.data?.card.link ?? domain.link}>
                          {domain.id === "sales" ? "店铺销售" : domain.label}
                        </Link>
                        <small>{state?.data?.card.valueLabel ?? "暂无可用期间"}</small>
                      </dt>
                      <dd>{rules[domain.id]}</dd>
                    </div>
                  );
                })}
              </dl>
            </article>
            <article className="m6-definition-risk">
              <h3>预警与动作</h3>
              <p>
                商品/推广按店铺＋链接去重，售后按报告、层级和对象计项；仅代表已接观察范围，不是全域正式异常数。
              </p>
              <p>
                商品/推广按来源优先级排序和筛选，同对象取最高来源等级，原等级保留；不作为中心正式分级。同对象建议归并，重复文本去重；未映射建议单列，不代表派单或进度。系统建议默认待核查；负责人、截止日及复盘日未指定时显示待分配。中心只展示子看板结果，不重新判断异常或分级。不同性质的影响金额不合计，也不生成统一评分。
              </p>
              <output className="m6-definition-counts" aria-label="当前观察覆盖">
                <span>{observed ? counts.observations : "—"} 条提示</span>
                <span>{counts.exceptionCount ?? "—"} 个对象</span>
                <span>未映射 {observed ? counts.unmappedObservations : "—"} 条</span>
                <span>
                  {actionsAvailable ? sourceActionRows : "—"} 条来源建议 ·{" "}
                  {actionsAvailable ? counts.actionRows : "—"} 条归并建议
                  {unmappedActions ? `（含未映射 ${unmappedActions} 条）` : ""}
                </span>
                <span>
                  可评估 {counts.availableDomains}/{coverage.length} 域
                </span>
              </output>
            </article>
          </div>
          <Collapse
            className="m6-source-disclosure"
            classNames={{
              header: "m6-source-disclosure-header",
              body: "m6-source-disclosure-body",
              icon: "m6-source-disclosure-icon",
            }}
            ghost
            expandIconPlacement="start"
            expandIcon={disclosureIcon}
            items={[
              {
                key: "sources",
                label: "详细来源与更新记录",
                children: <SourceDetails states={states} coverage={coverage} />,
              },
            ]}
          />
        </div>
      </article>
    </section>
  );
}
