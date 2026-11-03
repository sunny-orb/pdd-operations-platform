import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Button, Input, Pagination, Spin } from "antd";
import {
  diagnosis,
  diagnosisObjects,
  type DiagnosisData,
  type DiagnosisObject,
  type Metric,
} from "./contracts";
import { useResource, type PromotionState } from "./data";
import { changeValue, formatClue, formatMetric, formatValue, formatProductLevel } from "./format";

const comparison = (current: Metric | undefined, previous: Metric | undefined) =>
  `${formatMetric(previous)} → ${formatMetric(current)}（${changeValue(current?.change)}）`;
const roiLabel = (_metric: Metric | undefined, net = false) =>
  net ? "净投产" : "实际投产比（ROI）";
// P3仅给现有“观察”补展示代码，不给未评级或未触发的商品新增等级。
const priorityLabel = (priority: string) =>
  ({ P1: "P1 预警", P2: "P2 关注", 观察: "P3 观察" })[priority] ?? priority;
const ruleState = {
  triggered: "已触发",
  clear: "未触发",
  not_applicable: "未达到判断门槛",
  unavailable: "未评级",
};

function RuleDetails({
  item,
  showRecordedDays = true,
}: {
  item: DiagnosisObject | NonNullable<DiagnosisData["overall"]>;
  showRecordedDays?: boolean;
}) {
  // 相同原因只解释一次；仍列出受影响规则，不能把未评级显示为未触发。
  const limited = new Map<string, string[]>();
  item.rule_results
    .filter((rule) => rule.state === "unavailable")
    .forEach((rule) => {
      const reason = rule.reason || "本项所需数据未提供";
      limited.set(reason, [...(limited.get(reason) ?? []), rule.label]);
    });
  return (
    <details className="sop-rule-details">
      <summary>查看判断依据</summary>
      {item.rule_results
        .filter((rule) => rule.state !== "unavailable")
        .map((rule) => (
          <p key={rule.key}>
            {rule.label}：{ruleState[rule.state]}
            {rule.reason ? `（${rule.reason}）` : ""}
          </p>
        ))}
      {[...limited].map(([reason, names]) => (
        <p key={reason}>
          {reason}，{names.join("、")}本次未评级。
        </p>
      ))}
      {showRecordedDays && (
        <p>
          本期 / 前期有记录 {item.current_days} / {item.previous_days}{" "}
          天；没有记录的日期按已有记录统计。
        </p>
      )}
      {(["current", "previous"] as const).map((period) => {
        const excluded = item.exclusions[period];
        return (
          <div key={period}>
            {!!excluded.missing_spend_rows && (
              <p>
                {period === "current" ? "本期" : "前期"}
                {/[1-9]/.test(excluded.revenue_without_spend)
                  ? `有 ${formatValue(excluded.revenue_without_spend, "currency")} 成交未单独提供花费；成交照常计入，花费按已提供金额统计。`
                  : "部分商品花费未单独提供；成交照常计入，花费按已提供金额统计。"}
              </p>
            )}
            {!!excluded.missing_revenue_rows && (
              <p>{period === "current" ? "本期" : "前期"}部分交易额未提供，按已提供金额统计。</p>
            )}
          </div>
        );
      })}
    </details>
  );
}

/** 折叠区保留原生键盘交互；仅展开时挂载清单，不裁剪接口返回的诊断对象。 */
function DiagnosisDetails({
  summary,
  children,
  initialOpen = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  initialOpen?: boolean;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{summary}</summary>
      {open && children}
    </details>
  );
}

type ObjectGroup = "products" | "flagged_products" | "focused_products" | "shops" | "categories";
type RemoteList = {
  keyword: string;
  page: number;
  size: number;
  total: number;
  groupTotal: number;
  pending: boolean;
  error?: Error | null;
  retry: () => void;
  search: (value: string) => void;
  paginate: (page: number, size: number) => void;
};

function ObjectTable({ items, remote }: { items: DiagnosisObject[]; remote?: RemoteList }) {
  const [localKeyword, setKeyword] = useState("");
  const [localPage, setPage] = useState(1);
  const [localSize, setSize] = useState(25);
  const keyword = remote?.keyword ?? localKeyword;
  const page = remote?.page ?? localPage;
  const size = remote?.size ?? localSize;
  const needle = keyword.trim().toLocaleLowerCase();
  // 全量已返回对象先搜索再分页；服务端统计和原顺序不受本清单的浏览条件影响。
  const filtered = useMemo(
    () =>
      remote || !needle
        ? items
        : items.filter((item) =>
            [
              item.label,
              item.product_id,
              item.shop_name,
              item.operator,
              item.category,
              item.level,
              item.object_type === "product" ? formatProductLevel(item.level) : null,
              item.priority,
              priorityLabel(item.priority),
              ...Object.values(item.current).flatMap((metric) => [
                metric.label,
                metric.value,
                formatMetric(metric),
              ]),
              ...Object.values(item.previous).flatMap((metric) => [
                metric.label,
                metric.value,
                formatMetric(metric),
              ]),
              ...item.findings.flatMap((finding) => [
                finding.level,
                priorityLabel(finding.level),
                finding.rule,
                finding.evidence,
                finding.action,
              ]),
              ...item.clues.flatMap((clue) => [
                clue.label,
                clue.evidence,
                clue.action,
                formatClue(clue),
              ]),
              ...item.notes,
            ]
              .filter(Boolean)
              .join(" ")
              .toLocaleLowerCase()
              .includes(needle),
          ),
    [items, needle, remote],
  );
  const total = remote?.total ?? filtered.length;
  const groupTotal = remote?.groupTotal ?? items.length;
  const current = Math.min(page, Math.max(1, Math.ceil(total / size)));
  const visible = remote ? items : filtered.slice((current - 1) * size, current * size);
  return (
    <div className="sop-object-list">
      {(!!items.length || remote) && (
        <div className="sop-list-controls">
          <Input
            type="search"
            aria-label="搜索本清单全部对象"
            placeholder="搜索本清单全部对象、证据或建议"
            allowClear
            value={keyword}
            onChange={(event) => {
              if (remote) {
                remote.search(event.target.value);
                return;
              }
              setKeyword(event.target.value);
              setPage(1);
            }}
          />
          <span>
            {remote?.pending
              ? "正在更新清单…"
              : needle
                ? `匹配 ${total} / ${groupTotal} 个对象`
                : `共 ${groupTotal} 个对象`}
          </span>
        </div>
      )}
      {remote?.error && (
        <Alert
          type="error"
          title="清单暂时无法加载"
          action={<Button onClick={remote.retry}>重试</Button>}
        />
      )}
      {remote?.pending && <Spin size="small" aria-label="正在更新诊断清单" />}
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
            {visible.map((item) => (
              <tr key={item.object_key}>
                <td>
                  <b>
                    {item.object_type === "category" ? (item.category ?? "未归属品类") : item.label}
                  </b>
                  {item.object_type !== "shop" && (
                    <small>
                      {item.shop_name ?? "店铺资料缺失"}
                      {item.focus ? " · 重点款" : ""}
                    </small>
                  )}
                  {item.object_type === "product" && (
                    <small>等级：{formatProductLevel(item.level)}</small>
                  )}
                  <span>
                    {item.current_record_count === "0"
                      ? "本期无记录"
                      : formatMetric(item.current.spend)}{" "}
                    · {priorityLabel(item.priority)}
                  </span>
                  <small>
                    负责人：
                    {item.object_type === "product"
                      ? !item.operator || /未匹配|未分配/.test(item.operator)
                        ? "待分配"
                        : item.operator
                      : "按商品明细分配，见下方“全部商品扫描”。"}
                  </small>
                </td>
                <td>
                  <p>
                    {roiLabel(item.current.roi)} {comparison(item.current.roi, item.previous.roi)}
                  </p>
                  <p>
                    {roiLabel(item.current.net_roi, true)}{" "}
                    {comparison(item.current.net_roi, item.previous.net_roi)}
                  </p>
                  {item.findings.map((finding) => (
                    <p key={finding.rule}>
                      <b>
                        {priorityLabel(finding.level)} · {finding.rule}
                      </b>
                      <br />
                      {finding.evidence}
                    </p>
                  ))}
                  {!item.findings.length && (
                    <p>
                      {item.evaluation_state === "unrated"
                        ? "暂不能判断"
                        : "未触发已启用的推广异常规则；不代表利润达标。"}
                    </p>
                  )}
                  {item.notes.map((note) => (
                    <small key={note}>{note}</small>
                  ))}
                  <small>
                    本期 / 基期有记录日期：{item.current_days} / {item.previous_days}{" "}
                    天；缺行不补零。
                  </small>
                  <RuleDetails item={item} showRecordedDays={false} />
                </td>
                <td>
                  {[...new Set(item.findings.map((finding) => finding.action))].map((action) => (
                    <p key={action}>{action}</p>
                  ))}
                  {item.clues.map((clue) => (
                    <p key={clue.label}>
                      <b>{clue.label === "CTR" ? "点击率" : clue.label}线索：</b>
                      {formatClue(clue)}；{clue.action}
                    </p>
                  ))}
                  {!item.findings.length && !item.clues.length && (
                    <p>持续跟踪投产与成交承接，核对商品利润底线、退款及库存后再评估预算。</p>
                  )}
                  {item.focus && /新品/.test(item.level ?? "") && (
                    <p>新品结合测试阶段评估，不直接停投。</p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!filtered.length && !remote?.pending && !remote?.error && (
          <p className="m2-empty">
            {needle ? "没有匹配对象，请调整或清除本清单搜索。" : "此范围暂无对应对象。"}
          </p>
        )}
      </div>
      {!!total && (
        <Pagination
          current={current}
          pageSize={size}
          total={total}
          pageSizeOptions={[25, 50, 100]}
          showSizeChanger
          hideOnSinglePage={total <= 25}
          onChange={(nextPage, nextSize) => {
            if (remote) {
              remote.paginate(nextPage, nextSize);
              return;
            }
            setPage(nextSize !== size ? 1 : nextPage);
            setSize(nextSize);
          }}
        />
      )}
    </div>
  );
}

/** 清单展开才挂载；同一范围先完整搜索再取当前页，不提前下载折叠内容。 */
function RemoteObjectTable({ state, group }: { state: PromotionState; group: ObjectGroup }) {
  const [search, setSearch] = useState("");
  const [settledSearch, setSettledSearch] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  useEffect(() => {
    const timer = setTimeout(() => setSettledSearch(search), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const query = useResource(
    "diagnosis/objects",
    diagnosisObjects,
    "product",
    { ...state.queryScope, page, size },
    state.version,
    state.invalid,
    [],
    false,
    state.businessReady && search === settledSearch,
    { group, search },
  );
  useEffect(() => {
    if (query.error) state.stale(query.error);
  });
  const data = query.data?.data;
  return (
    <ObjectTable
      items={data?.items ?? []}
      remote={{
        keyword: search,
        page,
        size,
        total: data?.total ?? 0,
        groupTotal: data?.group_total ?? 0,
        pending: query.isPending,
        error: query.error,
        retry: () => void query.refetch(),
        search: (value) => {
          setSearch(value);
          setPage(1);
        },
        paginate: (nextPage, nextSize) => {
          setPage(nextSize === size ? nextPage : 1);
          setSize(nextSize);
        },
      }}
    />
  );
}

export function Diagnosis({ state }: { state: PromotionState }) {
  const query = useResource(
    "diagnosis",
    diagnosis,
    "product",
    state.queryScope,
    state.version,
    state.invalid,
    [],
    false,
    state.businessReady,
    { detail: "summary" },
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
      renderObjects={(group) => (
        <RemoteObjectTable
          key={JSON.stringify([group, state.version, { ...state.queryScope, page: 1, size: 25 }])}
          state={state}
          group={group}
        />
      )}
    />
  );
}
export function DiagnosisView({
  data,
  pending = false,
  error,
  retry,
  renderObjects,
}: {
  data: DiagnosisData | undefined;
  pending?: boolean;
  error?: Error | null;
  retry?: () => void;
  renderObjects?: (group: ObjectGroup) => ReactNode;
}) {
  const overall = data?.overall;
  const counts = data?.counts;
  const assessed = (counts?.fully_assessed ?? 0) + (counts?.partial ?? 0);
  // 与清单共用后端按等级、花费排好的结果；筛选后不保留旧商品或在前端重排。
  const firstFlagged = data?.flagged_products[0];
  const header = (
    <div className="panel-title">
      <div>
        <span>经营诊断 · SOP 规则分析</span>
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
  );
  if (error || pending || !data || counts?.products === 0)
    return (
      <section className="panel sop-diagnosis" aria-label="SOP经营诊断" id="promotion-diagnosis">
        {header}
        {error ? (
          <Alert
            type="error"
            title="SOP诊断查询未完成"
            description="本次诊断暂时无法显示，请重试。"
            action={<Button onClick={retry}>重试诊断</Button>}
          />
        ) : pending || !data ? (
          <Spin size="small" aria-label="正在查询经营诊断" />
        ) : (
          <p className="m2-empty">当前筛选范围没有推广数据。</p>
        )}
      </section>
    );
  return (
    <section className="panel sop-diagnosis" aria-label="SOP经营诊断" id="promotion-diagnosis">
      {header}
      <p className="sop-meta">
        诊断：{data.current_window.start} — {data.current_window.end}；对比：
        {data.previous_window.start} — {data.previous_window.end}。沿用上方筛选，比较相邻两个7天。
      </p>
      {overall?.current_record_count === "0" && (
        <p className="sop-meta">本期没有推广数据，前期记录可在下方清单查看。</p>
      )}
      <div className="sop-summary">
        <article>
          <span>经营结果</span>
          <h3>
            {roiLabel(overall?.current.roi)} {formatMetric(overall?.current.roi)}
          </h3>
          <p>
            花费 {formatMetric(overall?.current.spend)}；交易额{" "}
            {formatMetric(overall?.current.revenue)}。
          </p>
          <p>
            {roiLabel(overall?.current.roi)}{" "}
            {comparison(overall?.current.roi, overall?.previous.roi)}；
            {roiLabel(overall?.current.net_roi, true)}{" "}
            {comparison(overall?.current.net_roi, overall?.previous.net_roi)}。
          </p>
          <p>
            CPC {formatMetric(overall?.current.cpc)} · 点击率 {formatMetric(overall?.current.ctr)} ·
            点击转化率 {formatMetric(overall?.current.cvr)}。
          </p>
          {overall && <RuleDetails item={overall} />}
        </article>
        <article>
          <span>问题定位</span>
          <h3>
            {counts?.flagged
              ? `${counts.flagged} 个商品需复核`
              : assessed
                ? "已判断项目未触发提醒"
                : "暂不能判断"}
          </h3>
          {assessed > 0 && (
            <p>
              其中 {priorityLabel("P1")} {counts?.p1 ?? 0} 个，{priorityLabel("P2")}{" "}
              {counts?.p2 ?? 0} 个，{priorityLabel("观察")} {counts?.observation ?? 0}{" "}
              个。按店铺＋商品 ID 分开分析。
            </p>
          )}
          <p>实际与净投产分别判断；两期花费均≥¥300，下降15%为关注、25%为预警。</p>
          <p>
            已完整判断 {counts?.fully_assessed ?? 0} 款；部分可判断 {counts?.partial ?? 0}{" "}
            款；未评级 {counts?.unrated ?? 0} 款。
          </p>
          <p>
            {(counts?.limited ?? 0) > 0
              ? "各项分别判断；原因见下方“全部商品扫描”中各商品的“查看判断依据”。"
              : "同店同商品只计一次，按最需关注的情况归类。"}
          </p>
          {overall?.findings.slice(0, 3).map((finding) => (
            <p key={finding.rule}>
              <b>整体{priorityLabel(finding.level)}：</b>
              {finding.rule}；{finding.evidence}。
            </p>
          ))}
        </article>
        <article>
          <span>优先动作与机会</span>
          <h3>先核查原因，再决定预算</h3>
          <p>
            {firstFlagged
              ? `优先复核 ${firstFlagged.label}（花费 ${formatMetric(firstFlagged.current.spend)}），同级按花费排序；花费不是损失金额。`
              : assessed
                ? "暂无已确认阈值触发的商品预警，继续检查重点款的成本、转化和退款变化。"
                : "本次没有可评级的商品；已有花费、成交和判断依据见下方“全部商品扫描”。"}
          </p>
          <p>
            {firstFlagged
              ? "具体建议见下方“商品异常与行动清单”的“核查及行动建议”列。"
              : assessed
                ? "继续查看下方“重点款完整诊断”的“核查及行动建议”列。"
                : "展开下方“全部商品扫描”，点击各商品的“查看判断依据”。"}
          </p>
          <p>
            未接入同规格利润参数、库存与活动状态，暂不判定利润达标或建议放量。所有行动为待核查建议。
          </p>
        </article>
      </div>
      {!!overall?.clues.length && (
        <div className="sop-clues">
          <h3>汇总变化线索 · 原因待验证</h3>
          {overall.clues.map((clue) => (
            <p key={clue.label}>
              <b>{clue.label === "CTR" ? "点击率" : clue.label}：</b>
              {formatClue(clue)}。{clue.action}
            </p>
          ))}
          <small>CTR、CPC、点击转化暂无已确认独立预警阈值，变化不直接证明原因。</small>
        </div>
      )}
      <DiagnosisDetails initialOpen summary={<>商品异常与行动清单（{counts?.flagged ?? "—"}）</>}>
        {(counts?.flagged ?? 0) > 0 ? (
          renderObjects ? (
            renderObjects("flagged_products")
          ) : (
            <ObjectTable items={data.flagged_products} />
          )
        ) : (
          <p className="m2-empty">
            {assessed
              ? "已判断项目未触发提醒。"
              : "具体原因见下方“全部商品扫描”中各商品的“查看判断依据”。"}
          </p>
        )}
      </DiagnosisDetails>
      <DiagnosisDetails
        summary={<>重点款完整诊断：S、A、专供、新品，含多等级（{counts?.focused ?? "—"}）</>}
      >
        {renderObjects ? (
          renderObjects("focused_products")
        ) : (
          <ObjectTable items={data.focused_products} />
        )}
      </DiagnosisDetails>
      <DiagnosisDetails
        summary={
          <>
            按店铺 / 品类下钻（{counts?.shops ?? "—"} 家店铺、{counts?.categories ?? "—"}{" "}
            个店铺品类组合）
          </>
        }
      >
        <p className="sop-list-note">
          店铺、店铺内品类分别汇总；与商品清单有包含关系，金额不要重复相加。
        </p>
        <h3>店铺</h3>
        {renderObjects ? renderObjects("shops") : <ObjectTable items={data.shops} />}
        <h3>店铺内品类</h3>
        {renderObjects ? renderObjects("categories") : <ObjectTable items={data.categories} />}
      </DiagnosisDetails>
      <DiagnosisDetails summary={<>全部商品扫描（{counts?.products ?? "—"}）</>}>
        {renderObjects ? renderObjects("products") : <ObjectTable items={data.products} />}
        {data.coverage.observed_through && (
          <p className="sop-list-note">数据截至：{data.coverage.observed_through}。</p>
        )}
        {counts?.current_records != null && <p>本期统计{counts.current_records}条推广记录。</p>}
        {counts?.unmatched_records != null && counts?.missing_product_ids != null && (
          <p>
            商品资料未匹配完整{counts.unmatched_records}条，缺少商品ID{counts.missing_product_ids}
            条。 已有金额照常计入，不补写等级或运营人员。
          </p>
        )}
      </DiagnosisDetails>
      <DiagnosisDetails summary="查看判断规则">
        <div className="sop-table">
          <table>
            <thead>
              <tr>
                <th>项目</th>
                <th>判断规则</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>诊断期间</td>
                <td>
                  截至所选结束日的近7天，与前7天比较；最晚取已收到数据的日期和昨日。开始日期不改变这两个周期。
                </td>
              </tr>
              <tr>
                <td>投产比 / 净投产</td>
                <td>
                  分别判断；两期花费均≥300元，前期投产为正，下降≥15%为P2 关注，下降≥25%为P1 预警。
                </td>
              </tr>
              <tr>
                <td>花费增长</td>
                <td>
                  花费增长≥20%，交易额增幅不足花费增幅一半时为P2 关注；同一商品已有P1 预警时归入P1。
                </td>
              </tr>
              <tr>
                <td>有花费无成交</td>
                <td>
                  本期花费≥300元、成交0笔时列为P3 观察；结合后续成交和测试阶段再决定，不直接停投。
                </td>
              </tr>
              <tr>
                <td>没有记录 / 暂不能判断</td>
                <td>
                  某天没有记录，照常统计已有数据；某项整期都没提供数值、前期无数据或历史不足时，只限制受影响的项目。0仍按真实0计算。
                </td>
              </tr>
              <tr>
                <td>缺少花费等数据</td>
                <td>
                  交易额和已提供的花费分别合计；能算出结果且达到门槛，就列出提醒。花费“—”仍保留；提醒供复核，调整预算前还要核对实际费用。
                </td>
              </tr>
              <tr>
                <td>商品与汇总</td>
                <td>
                  同店同商品只计一次；多等级含A级、S级、专供或新品的纳入重点款。店铺、品类是各自汇总，不与商品金额重复相加。
                </td>
              </tr>
              <tr>
                <td>变化线索与建议</td>
                <td>
                  点击率下降、点击成本上升、点击转化率下降时，显示对应核查方向；没有单独的P级阈值，不直接证明原因。
                </td>
              </tr>
              <tr>
                <td>建议怎么产生</td>
                <td>
                  先用当前筛选数据算指标、匹配规则，再显示该规则对应的核查提示，不是AI生成。每个商品的触发数值看“证据与规则”列，具体建议看同一行“核查及行动建议”列。
                </td>
              </tr>
              <tr>
                <td>去年同期在哪里</td>
                <td>
                  看<a href="#promotion-trend">上方“核心指标趋势”</a>
                  的同色虚线；有去年数据才显示。本区只比较相邻两个7天，不按去年同期评级。
                </td>
              </tr>
              <tr>
                <td>其他核查在哪里</td>
                <td>
                  店铺、品类变化看上方“按店铺 /
                  品类下钻”；具体出价、素材和投放状态需到拼多多推广后台的对应商品计划核对。利润、库存和活动尚未纳入本区判断，不能据此认定利润达标或直接放量。
                </td>
              </tr>
              <tr>
                <td>负责人与执行情况</td>
                <td>
                  “负责人”取自商品上架资料，不代表已经指派任务。这里提供核查建议，尚不记录执行状态、目标、截止时间或复盘结果。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </DiagnosisDetails>
    </section>
  );
}
