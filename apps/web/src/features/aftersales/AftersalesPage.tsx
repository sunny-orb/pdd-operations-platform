import { useState } from "react";
import { Alert, Button, Collapse } from "antd";
import {
  BarChartOutlined,
  BulbOutlined,
  FilterOutlined,
  LineChartOutlined,
  TableOutlined,
} from "@ant-design/icons";
import { DashboardLayout } from "../../shared/layouts/DashboardLayout";
import { useAftersales } from "./data";
import { Filters } from "./Filters";
import { Brief } from "./Brief";
import { Details } from "./Details";
import { Charts } from "./Charts";
import { Analysis } from "./Analysis";
import { StableContent } from "./StableContent";
import "./aftersales.css";
const sections = [
  { id: "aftersales-filters", title: "报告筛选", icon: <FilterOutlined /> },
  { id: "aftersales-decision", title: "决策摘要", icon: <BulbOutlined /> },
  { id: "aftersales-details", title: "指标与整改", icon: <BarChartOutlined /> },
  { id: "aftersales-charts", title: "历史与原因", icon: <LineChartOutlined /> },
  { id: "aftersales-analysis", title: "分析明细", icon: <TableOutlined /> },
];
/** 售后整页保留原型展示顺序；筛选移正文，共享导航不承载业务查询。 */
export function AftersalesPage() {
  const [expanded, setExpanded] = useState(false);
  const state = useAftersales(expanded);
  const data = state.overview.data?.data;
  const meta = state.meta.isPlaceholderData ? state.overview.data?.meta : state.meta.data?.data;
  const sourceFailed = meta?.sources.some(
    (source) =>
      source.error_code || ["failed_retained", "source_unavailable"].includes(source.status),
  );
  const pendingReport = meta?.sources.some(
    (source) => source.status === "new_input_pending_report",
  );
  const report = data?.report;
  const errors = [
    state.meta.error,
    state.options.error,
    state.overview.error,
    state.analysis.error,
  ].filter(Boolean);
  const openDetails = (target: string) => {
    setExpanded(true);
    requestAnimationFrame(() =>
      document.getElementById(target)?.scrollIntoView?.({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      }),
    );
  };
  const rolling =
    report != null &&
    (report.report_type === "weekly" || report.report_type === "custom") &&
    (Date.parse(report.period_end) - Date.parse(report.period_start)) / 86400000 === 29;
  return (
    <DashboardLayout currentPage="aftersales" sections={sections} context="售后分析">
      <main className="m5-aftersales-scope">
        <section className="m5-hero">
          <div>
            <p className="m5-hero-eyebrow">AFTERSALES · RETURN ANALYSIS</p>
            <h1>退货分析看板</h1>
            <p className="m5-hero-description">
              按报告周期查看退货、退款与原因分布，定位品类及单款问题，跟进整改。
            </p>
          </div>
          <div className="m5-snapshot">
            <i aria-hidden="true" />
            数据截止：<time>{meta?.observed_to ?? "—"}</time>
          </div>
        </section>
        <Filters state={state} />
        <div className="m5-request-status" aria-live="polite">
          {errors.length > 0 && (
            <Alert
              type="error"
              showIcon
              title={errors[0] instanceof Error ? errors[0].message : "售后查询失败"}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void state.meta.refetch();
                    void state.options.refetch();
                    void state.overview.refetch();
                    void state.analysis.refetch();
                  }}
                >
                  重试本地查询
                </Button>
              }
            />
          )}
        </div>
        <StableContent
          pending={!state.meta.isError && (state.meta.isPending || state.overview.isPending)}
          ready={Boolean(data)}
          label="正在更新报告…"
        >
          {(meta?.status === "source_unavailable" || sourceFailed || pendingReport) && (
            <Alert
              className="m5-source-alert"
              type="warning"
              showIcon
              title={
                meta?.status === "source_unavailable"
                  ? "售后真实来源尚未齐备"
                  : sourceFailed
                    ? "最近数据接收未成功，当前显示已发布报告"
                    : "新数据已接收，当前仍显示已发布报告"
              }
            />
          )}
          <div className="m5-report-summary">
            <section className="m5-report-heading">
              <div>
                <h2>
                  {report
                    ? rolling
                      ? "近 30 天退货分析 · 周报"
                      : report.label
                    : "等待可核售后报告"}
                </h2>
                <p>
                  {report
                    ? `${report.period_start} 至 ${report.period_end}`
                    : "尚无已发布的相容报告期间"}
                </p>
              </div>
              <div className="m5-report-links">
                {[
                  { label: "飞书文档", url: report?.document_url },
                  { label: "分析表格", url: report?.sheet_url },
                ].map((link) => (
                  <Button
                    key={link.label}
                    href={link.url || undefined}
                    target="_blank"
                    rel="noreferrer"
                    disabled={!link.url}
                  >
                    {link.label}
                  </Button>
                ))}
              </div>
            </section>
            <Brief data={data} openDetails={openDetails} />
          </div>
          <section id="aftersales-details" className="m5-details">
            <Collapse
              ghost
              activeKey={expanded ? ["details"] : []}
              onChange={(keys) => setExpanded(keys.includes("details"))}
              items={[
                {
                  key: "details",
                  label: "指标与整改明细",
                  children: expanded ? (
                    <Details
                      data={state.details.data?.data ?? data}
                      meta={meta}
                      actionsPending={!state.details.isError && state.details.isPending}
                      actionsError={state.details.isError}
                      retryActions={() => void state.details.refetch()}
                    />
                  ) : null,
                },
              ]}
            />
          </section>
          <Charts data={data} />
        </StableContent>
        <Analysis state={state} />
      </main>
    </DashboardLayout>
  );
}
