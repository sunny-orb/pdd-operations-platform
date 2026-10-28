import { useState } from "react";
import { Alert, Button, Collapse, Spin } from "antd";
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
import { SourceState } from "./SourceState";
import { Brief } from "./Brief";
import { Details } from "./Details";
import { Charts } from "./Charts";
import { Analysis } from "./Analysis";
import { localTime } from "./format";
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
  const state = useAftersales();
  const [expanded, setExpanded] = useState(false);
  const data = state.overview.data?.data;
  const meta = state.meta.data?.data;
  const report = data?.report;
  const errors = [
    state.meta.error,
    state.options.error,
    state.overview.error,
    state.analysis.error,
  ].filter(Boolean);
  const openDetails = () => {
    setExpanded(true);
    requestAnimationFrame(() =>
      document.getElementById("aftersales-details")?.scrollIntoView({
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
    <DashboardLayout
      currentPage="aftersales"
      sections={sections}
      context="售后分析 · 报告期间独立分析"
    >
      <main className="m5-aftersales-scope">
        <section className="m5-hero">
          <div>
            <span className="m5-eyebrow">售后经营 · 原因与改进</span>
            <h1>退货分析看板</h1>
            <p>实退、销售与原因结构按实际报告期间追溯</p>
          </div>
          <div className="m5-snapshot">
            <b>观察截至：{meta?.observed_to ?? "未取得可核数据"}</b>
            <span>完整截至日：{meta?.complete_through ?? "未知"}</span>
            <span>最近成功：{localTime(meta?.last_success_at)}</span>
          </div>
        </section>
        <SourceState meta={meta} />
        <Filters state={state} />
        <div className="m5-request-status" aria-live="polite">
          {state.meta.isPending && <Spin size="small" aria-label="正在读取售后来源" />}
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
        {meta?.status === "source_unavailable" && (
          <Alert
            className="m5-source-alert"
            type="warning"
            showIcon
            title="售后真实来源尚未齐备"
            description="保留原展示字段；缺少可核来源的金额、数量和退款率显示为不可用，不以历史快照或其他看板金额替代。"
          />
        )}
        <section className="m5-report-heading">
          <div>
            <h2>
              {report
                ? rolling
                  ? `近30天退货分析 · ${report.report_type === "weekly" ? "周报" : "滚动报告"}`
                  : report.label
                : "等待可核售后报告"}
            </h2>
            <p>
              {report
                ? `${report.period_start} 至 ${report.period_end}${rolling ? "（滚动30天，非自然周）" : ""}`
                : "尚无已发布的相容报告期间"}
            </p>
            <small>报告生成：{localTime(report?.generated_at)}</small>
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
        <section id="aftersales-details" className="m5-details">
          <Collapse
            activeKey={expanded ? ["details"] : []}
            onChange={(keys) => setExpanded(keys.includes("details"))}
            items={[{ key: "details", label: "指标与整改明细", children: <Details data={data} /> }]}
          />
        </section>
        <Charts data={data} />
        <Analysis state={state} />
        <footer className="m5-footer">
          金额仅展示已核来源原值，未通过口径核验的金额保持不可用；三率独立计算，不相加。建议和分析状态不代表人工处理进度。
        </footer>
      </main>
    </DashboardLayout>
  );
}
