import { useState } from "react";
import { Alert, Button, Card, Collapse, Empty, Segmented, Spin, Table, Tag, Tooltip } from "antd";
import {
  BellOutlined,
  CloudOutlined,
  DashboardOutlined,
  ReadOutlined,
  ReloadOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { useOverview } from "./useOverview";
import type { DomainState } from "./data";
import {
  centerCounts,
  domains,
  formatMetric,
  localTime,
  shownAlerts,
  type DomainId,
  type Field,
  type SuggestedAction,
} from "./model";
import { WeatherSection } from "./WeatherSection";
import "./overview.css";
const sections = [
  { id: "overview", title: "经营总览", icon: <DashboardOutlined /> },
  { id: "alerts", title: "经营预警", icon: <BellOutlined /> },
  { id: "actions", title: "本日动作", icon: <UnorderedListOutlined /> },
  { id: "weather", title: "全国关键天气", icon: <CloudOutlined /> },
  { id: "definitions", title: "指标口径", icon: <ReadOutlined /> },
];
const fallbackPairs: Record<DomainId, string[]> = {
  sales: ["最新入仓日 GMV", "月度目标", "目标达成", "最近更新"],
  products: ["日环比", "同比", "异常数", "最近更新"],
  promotion: ["花费", "交易额", "CTR / CVR", "最近更新"],
  aftersales: ["退款总额", "售后退款", "异常数", "最近更新"],
};
function renderField(field: Field) {
  return field.type === "text" ? (field.value ?? "—") : formatMetric(field.value, field.type);
}
function DomainCard({ state }: { state: DomainState }) {
  const card = state.data?.card;
  const domain = domains.find((d) => d.id === state.id)!;
  const pairs: Field[] = card?.pairs.length
    ? card.pairs
    : fallbackPairs[state.id].map((label) => ({ label, value: null, type: "text" as const }));
  return (
    <Card id={`domain-${state.id}`} className={`m6-domain-card m6-${state.id}`}>
      <div className="m6-card-head">
        <span>{card?.title ?? domain.label}</span>
        <Tag>{card?.status ?? (state.loading ? "读取中" : "待核来源")}</Tag>
      </div>
      <h3>{card?.heading ?? `${domain.label}看板`}</h3>
      <div className="m6-metric-line">
        <strong>
          {card
            ? `${state.id === "promotion" ? "ROI " : ""}${formatMetric(card.value, card.valueType)}`
            : "—"}
        </strong>
        <span>{card?.valueLabel ?? "尚无可核数据"}</span>
      </div>
      <div className="m6-metric-pairs">
        {pairs.map((p) => (
          <p key={p.label}>
            <span>{p.label}</span>
            <Tooltip title={p.note}>
              <b>{renderField(p)}</b>
            </Tooltip>
          </p>
        ))}
      </div>
      {state.loading && (
        <div className="m6-card-state">
          <Spin size="small" /> 正在读取本地发布数据
        </div>
      )}
      {state.error && (
        <Alert
          type="error"
          showIcon
          title={state.error}
          action={
            <Button size="small" onClick={state.refresh}>
              重试
            </Button>
          }
        />
      )}
      <footer>
        <small>{card?.note ?? "保留原字段；缺失、读取失败与真实零分别表达。"}</small>
        <Link to={card?.link ?? domain.link}>查看数据 →</Link>
      </footer>
      <div className="m6-source-details">
        <span>
          观察截至 {state.meta?.date ?? "未知"} · 完整截至 {state.meta?.completeThrough ?? "未知"}
        </span>
        <span>最近成功 {localTime(state.meta?.success)}</span>
        <span>
          最近尝试 {localTime(state.meta?.attempt)} · {state.meta?.attemptStatus ?? "尚无状态"}
        </span>
        <span>来源状态 {state.meta?.status ?? "未知"}</span>
        {state.meta?.notes.map((note) => (
          <span key={note}>{note}</span>
        ))}
      </div>
    </Card>
  );
}
const columns = [
  {
    title: "问题",
    key: "issue",
    width: 200,
    render: (_: unknown, r: SuggestedAction) => <Link to={r.link}>{r.issue}</Link>,
  },
  {
    title: "影响金额",
    key: "impact",
    width: 175,
    render: (_: unknown, r: SuggestedAction) => (
      <>
        <b>{formatMetric(r.impact, "currency")}</b>
        <small className="m6-table-note">{r.impactKind}</small>
      </>
    ),
  },
  { title: "原因假设", dataIndex: "hypothesis", width: 240 },
  { title: "动作", dataIndex: "suggestion", width: 280 },
  { title: "负责人", dataIndex: "assignee", width: 100 },
  { title: "截止时间", dataIndex: "due", width: 100 },
  {
    title: "当前进度",
    dataIndex: "progress",
    width: 140,
    render: (value: string) => <Tag>{value}</Tag>,
  },
  { title: "验证指标", dataIndex: "verification", width: 260 },
];
/** 中心只复用负责域结果，保留线上字段顺序；未知治理口径不升级为业务结论。 */
export function OverviewPage() {
  const states = useOverview();
  const [level, setLevel] = useState("全部");
  const observations = states.flatMap((s) => s.data?.observations ?? []);
  const actions = states.flatMap((s) => s.data?.actions ?? []);
  const counts = centerCounts(observations, actions);
  const hasResults = states.some((s) => s.data);
  const actionCount = hasResults ? counts.actionRows : "—";
  const visible = shownAlerts(observations, level);
  const sales = states.find((s) => s.id === "sales")?.data?.card;
  const aftersales = states.find((s) => s.id === "aftersales")?.data?.card;
  const target = sales?.pairs.find((p) => p.label === "目标达成")?.value;
  const refreshing = states.some((s) => s.refreshing);
  const loading = states.some((s) => s.loading);
  const failed = states.filter((s) => s.error).length;
  const sourceUpdateFailed = states.some((s) => s.meta?.attemptStatus === "failed");
  const dataStatus = loading
    ? "正在读取各域"
    : failed
      ? `${failed} 域读取失败`
      : sourceUpdateFailed
        ? "已发布 · 最近更新失败"
        : states.every((s) => s.data)
          ? "已发布 · 完整性待核"
          : "来源未齐备";
  return (
    <DashboardLayout
      currentPage="overview"
      sections={sections}
      context="运营中心 · 各域按实际期间展示"
    >
      <main className="m6-overview-scope">
        <section className="m6-scopebar" aria-label="数据更新时间和口径入口">
          <span>
            <i />
            数据截至：
            {[states[1], states[2], states[0], states[3]].map((s, i) => (
              <span key={s.id}>
                {i ? " · " : ""}
                {domains.find((d) => d.id === s.id)?.label} {s.meta?.date ?? "待读取"}
              </span>
            ))}{" "}
            <Tag>本地发布数据</Tag>
          </span>
          <a href="#definitions">查看统一指标口径 →</a>
        </section>
        <section className="m6-hero" id="overview">
          <div className="m6-hero-copy">
            <p className="m6-eyebrow">OPERATIONS CONTROL · PDD</p>
            <h1>一屏掌握经营重点</h1>
            <p>
              当前范围：全部店铺 ·
              各域最新可用观察。中心页汇总销售、商品、推广与售后数据，让今天需要核查的事项先浮出来。
            </p>
            <div className="m6-focus-row">
              <span>
                <i className="blue" />
                店铺 GMV <b>{formatMetric(sales?.value, "currency")}</b>
              </span>
              <span>
                <i className="green" />
                目标达成 <b>{formatMetric(target, "percent")}</b>
              </span>
              <span>
                <i className="amber" />
                待办动作 <b>{actionCount} 条建议</b>
              </span>
            </div>
            <div className="m6-quick-links">
              {domains.map((d, i) => (
                <Link to={d.link} key={d.id}>
                  <span>{["店铺销售", "商品经营", "推广效率", "售后退款"][i]}</span>
                  <b>
                    {
                      [
                        "查看 GMV 原始数据 →",
                        "进入商品数据看板 →",
                        "进入推广数据看板 →",
                        "进入退货分析看板 →",
                      ][i]
                    }
                  </b>
                </Link>
              ))}
            </div>
          </div>
          <aside className="m6-risk-callout">
            <span>需优先处理</span>
            <strong>— 项 P0 风险</strong>
            <p>
              售后退款率 <b>{formatMetric(aftersales?.value, "percent")}</b>
              。正式风险与计数口径待确认，先按来源证据复核。
            </p>
            <a href="#alerts">
              查看预警与处理建议 <b>→</b>
            </a>
          </aside>
        </section>
        <section className="m6-kpi-grid" aria-label="经营总览关键数据">
          <Card>
            <span>数据状态</span>
            <b>{dataStatus}</b>
            <small>商品 / 推广按观察日；售后按实际报告期间</small>
          </Card>
          <Card>
            <span>异常数量</span>
            <b>
              — <em>项</em>
            </b>
            <small>P0 — 项 · P1 — 项 · P2 — 项</small>
            <small>M6-01 待确认</small>
          </Card>
          <Card>
            <span>待办动作</span>
            <b>
              {actionCount} <em>条建议</em>
            </b>
            <small>建议清单，尚未登记为处理工单</small>
          </Card>
          <Card>
            <span>本日负责人</span>
            <b>
              — <em>人</em>
            </b>
            <small>负责人未登记，不从来源昵称推定</small>
          </Card>
        </section>
        <section className="m6-section">
          <div className="m6-section-title">
            <div>
              <p className="m6-eyebrow">CONNECTED DASHBOARDS</p>
              <h2>经营总览</h2>
            </div>
            <div className="m6-heading-tools">
              <span>各域版本和期间独立，完整日未知</span>
              <Button
                icon={<ReloadOutlined />}
                loading={refreshing}
                onClick={() => states.forEach((s) => s.refresh())}
              >
                刷新本地查询
              </Button>
            </div>
          </div>
          <div className="m6-dashboard-grid">
            {states.map((state) => (
              <DomainCard key={state.id} state={state} />
            ))}
          </div>
        </section>
        <section className="m6-section" id="alerts">
          <div className="m6-section-title">
            <div>
              <p className="m6-eyebrow">ALERT CENTER</p>
              <h2>经营预警中心</h2>
            </div>
            <Segmented
              aria-label="预警等级"
              options={["全部", "P0", "P1", "P2"]}
              value={level}
              onChange={setLevel}
            />
          </div>
          <p className="m6-method">
            当前已取得列表 {hasResults ? counts.observations : "—"}{" "}
            条观察提示；正式异常总数及等级数量未发布。M1 / M2
            源域等级仅保留线索，完整性与归因待核。综合评分不适用；等级筛选只作用于下方预警列表。
          </p>
          <div className="m6-alert-list">
            {visible.length ? (
              visible.map((item) => (
                <article className="m6-alert-row" key={item.id}>
                  <div className="m6-priority">
                    <Tag color="blue">观察</Tag>
                    {item.sourcePriority && <small>源域 {item.sourcePriority}</small>}
                  </div>
                  <div className="m6-alert-description">
                    <h3>{item.title}</h3>
                    <p>{item.detail}</p>
                    <small>对应筛选：{item.scope}</small>
                  </div>
                  <div className="m6-impact">
                    <span>影响金额 / 样本</span>
                    <b>{formatMetric(item.impact, "currency")}</b>
                    <small>{item.impactKind}</small>
                    <small>综合评分：不适用</small>
                  </div>
                  <Link to={item.link}>定位处理 →</Link>
                </article>
              ))
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  level === "全部"
                    ? "暂无已取得的观察提示；来源未齐备不代表没有风险"
                    : "尚无通过中心规则确认的该等级项，可在“全部”查看观察提示"
                }
              />
            )}
          </div>
        </section>
        <section className="m6-section" id="actions">
          <div className="m6-section-title">
            <div>
              <p className="m6-eyebrow">DAILY ACTIONS</p>
              <h2>本日经营动作</h2>
            </div>
            <p>来源建议；负责人、截止时间与进度待登记</p>
          </div>
          <Table
            className="m6-action-table"
            columns={columns}
            dataSource={actions}
            rowKey="id"
            pagination={{
              defaultPageSize: 10,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (n) => `当前清单 ${n} 条建议`,
            }}
            scroll={{ x: 1495 }}
            locale={{ emptyText: "尚未取得可核来源建议" }}
          />
        </section>
        <WeatherSection />
        <section id="definitions" className="m6-definitions">
          <Collapse
            items={[
              {
                key: "definitions",
                label: (
                  <div>
                    <p className="m6-eyebrow">DATA GOVERNANCE</p>
                    <h2>统一筛选与指标口径</h2>
                  </div>
                ),
                children: (
                  <div className="m6-definition-grid">
                    <p>
                      <b>时间与店铺</b>
                      各域默认取最新可用观察日，不能把观察日写成完整日。日期、完整截至日与同步状态逐域显示；跳转携带可支持的范围。
                    </p>
                    <p>
                      <b>商品与推广</b>来源：已接入的商品、推广本地 API。GMV、花费、归因交易额与 ROI
                      各按负责域原口径；商品风险与推广诊断另有观察窗，不等于卡片当日。
                    </p>
                    <p>
                      <b>店铺 GMV 与目标</b>
                      来源：数仓店铺销售。月累计按销售域当前经营月；月目标未取得时保持缺失。店铺页当前入口需进入后选择范围。
                    </p>
                    <p>
                      <b>售后退款</b>
                      来源：售后实际报告和原报告期间。金额与退款率沿用源域核验门禁；缺失不以旧报告或其他口径补齐，整体/品类/单款金额不重复加总。
                    </p>
                    <p>
                      <b>风险与动作</b>当前来源线索均作观察，M6-01
                      正式计数单位待确认；不套用原型跨域加权评分。建议不代表派单或进度。页面刷新只读本地发布数据，不触发上游或发送消息。
                    </p>
                  </div>
                ),
              },
            ]}
          />
        </section>
        <footer className="m6-page-footer">
          最近成功时间、观察截至与完整截至分别展示。金额性质与期间不同，不相加；页面每 45
          秒及窗口聚焦检查本地元信息，发布变版后重取。
        </footer>
      </main>
    </DashboardLayout>
  );
}
