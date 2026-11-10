import {
  BellOutlined,
  CloudOutlined,
  DashboardOutlined,
  ReadOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useState } from "react";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { useOverview } from "./useOverview";
import { buildCenterView, domains, formatMetric, observationCoverage } from "./model";
import { WeatherSection } from "./WeatherSection";
import { SummaryCard } from "./SummaryCard";
import { DomainCard } from "./DomainCard";
import { DefinitionsSection } from "./DefinitionsSection";
import { AlertCenter } from "./AlertCenter";
import { ActionsSection } from "./ActionsSection";
import { DataUpdateStatus } from "./DataUpdateStatus";
import { homePreview } from "./presentation";
import "./overview.css";
const sections = [
  { id: "overview", title: "经营总览", icon: <DashboardOutlined /> },
  { id: "alerts", title: "经营预警", icon: <BellOutlined /> },
  { id: "actions", title: "本日动作", icon: <UnorderedListOutlined /> },
  { id: "weather", title: "全国关键天气", icon: <CloudOutlined /> },
  { id: "definitions", title: "指标口径", icon: <ReadOutlined /> },
];
/** 中心只复用负责域结果，保留线上字段顺序；未知治理口径不升级为业务结论。 */
export function OverviewPage() {
  const states = useOverview();
  const observations = states.flatMap((s) => s.data?.observations ?? []);
  const actions = states.flatMap((s) => s.data?.actions ?? []);
  const coverage = observationCoverage(states);
  const view = buildCenterView(observations, actions, coverage);
  const { counts } = view;
  const preview = homePreview(view.alerts, view.actions);
  const [allAlerts, setAllAlerts] = useState(false);
  const [allActions, setAllActions] = useState(false);
  const attentionCount = preview.alerts.length || (counts.availableDomains ? 0 : "—");
  const actionCount = view.actionCount === null ? "—" : preview.actions.length;
  const sales = states.find((s) => s.id === "sales")?.data?.card;
  const aftersales = states.find((s) => s.id === "aftersales")?.data?.card;
  const target = sales?.pairs.find((p) => p.label === "目标达成")?.value;
  const loading = states.some((s) => s.loading);
  const failed = states.filter((s) => s.error).length;
  const sourceUpdateFailed = states.some((s) => s.meta?.attemptStatus === "failed");
  const dataStatus = loading
    ? "正在读取"
    : failed
      ? "部分数据暂不可用"
      : sourceUpdateFailed
        ? "最近更新失败"
        : states.every((s) => s.data)
          ? "平台数据可用"
          : "正在等待数据";
  return (
    <DashboardLayout
      currentPage="overview"
      sections={sections}
      context="运营中心 · 各域按实际期间展示"
    >
      <main className="m6-overview-scope">
        <section className="m6-scopebar" aria-label="数据更新时间和口径入口">
          <span className="m6-scope-note">
            <i />
            数据截至：
            {domains.map((domain, i) => (
              <span className="m6-scope-domain" key={domain.id}>
                {i ? " · " : ""}
                {domain.id === "sales" ? "店铺销售" : domain.label}{" "}
                {states.find((state) => state.id === domain.id)?.meta?.date ?? "待读取"}
              </span>
            ))}{" "}
            <b className="m6-data-source">数仓＋飞书</b>
          </span>
          <div className="m6-scope-actions">
            <a href="#definitions">查看统一指标口径 →</a>
            <DataUpdateStatus />
          </div>
        </section>
        <section className="m6-hero" id="overview">
          <div className="m6-hero-copy">
            <p className="m6-eyebrow">OPERATIONS CONTROL · PDD</p>
            <h1>一屏掌握经营重点</h1>
            <p>
              当前范围：全部店铺 ·
              最新可用数据。中心页汇总销售、商品、推广与售后数据，让今天最需要处理的事项先浮出来。
            </p>
            <div className="m6-focus-row">
              <span>
                <i className="blue" />
                店铺 GMV <b>{formatMetric(sales?.value, "currency")}</b>
              </span>
              <span>
                <i className="green" />
                目标达成 <b>{formatMetric(target, "percent", 1)}</b>
              </span>
              <a href="#actions" onClick={() => setAllActions(false)}>
                <i className="amber" />
                待办动作 <b>{actionCount} 项</b>
              </a>
            </div>
            <div className="m6-quick-links">
              {domains.map((d, i) => (
                <Link to={states.find((s) => s.id === d.id)?.data?.card.link ?? d.link} key={d.id}>
                  <span>{["店铺销售", "商品数据", "推广效率", "售后退款"][i]}</span>
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
            <strong>{view.sourceCounts?.P0 ?? "—"} 项 P0 风险</strong>
            <small className="m6-risk-scope">商品 / 推广已接范围 · 来源等级</small>
            <p>
              售后退款率 <b>{formatMetric(aftersales?.value, "percent")}</b>
              {aftersales?.value != null
                ? "，建议从影响金额最高的事项开始闭环。"
                : "。查看各看板的异常提示与处理建议。"}
            </p>
            <a href="#alerts">
              查看预警与处理建议 <b>→</b>
            </a>
          </aside>
        </section>
        <section className="m6-kpi-grid" aria-label="经营总览关键数据">
          <SummaryCard
            label="数据状态"
            value={dataStatus}
            tone="green"
            description={
              sourceUpdateFailed
                ? "当前展示已发布数据，更新状态见页顶入口"
                : "商品 / 推广日更；售后按报告期间更新"
            }
          />
          <SummaryCard
            label="异常数量"
            value={attentionCount}
            unit="项"
            tone="amber"
            href="#alerts"
            onNavigate={() => setAllAlerts(false)}
            description={
              <>
                <span>首页展示，非全部异常</span>
                <br />
                <a href="#alerts" onClick={() => setAllAlerts(true)}>
                  查看全部预警 →
                </a>
              </>
            }
          />
          <SummaryCard
            label="待办动作"
            value={actionCount}
            unit="项"
            tone="blue"
            href="#actions"
            onNavigate={() => setAllActions(false)}
            description={
              <>
                <span>首页建议 · 待核查，尚未派单</span>
                <br />
                <a href="#actions" onClick={() => setAllActions(true)}>
                  查看全部建议 →
                </a>
              </>
            }
          />
          <SummaryCard
            label="本日负责人"
            value={counts.assignees ?? "待分配"}
            unit={counts.assignees === null ? undefined : "人"}
            tone="purple"
            description="尚未指派负责人"
          />
        </section>
        <section className="m6-section m6-connected-section">
          <div className="m6-section-title">
            <div>
              <p className="m6-eyebrow">CONNECTED DASHBOARDS</p>
              <h2>经营总览</h2>
            </div>
            <div className="m6-heading-tools">
              <span>经营数据来自数仓，GMV目标来自飞书</span>
            </div>
          </div>
          <div className="m6-dashboard-grid">
            {states.map((state) => (
              <DomainCard key={state.id} state={state} />
            ))}
          </div>
        </section>
        <AlertCenter
          observations={view.alerts}
          preview={preview.alerts}
          available={counts.availableDomains > 0}
          showAll={allAlerts}
          onShowAllChange={setAllAlerts}
        />
        <ActionsSection
          coverage={coverage}
          actions={view.actions}
          preview={preview.actions}
          total={view.actionCount}
          showAll={allActions}
          onShowAllChange={setAllActions}
        />
        <WeatherSection />
        <DefinitionsSection
          states={states}
          counts={counts}
          coverage={coverage}
          sourceActionRows={view.sourceActionRows}
          unmappedActions={view.unmappedActions}
        />
      </main>
    </DashboardLayout>
  );
}
