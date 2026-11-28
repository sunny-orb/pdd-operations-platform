import { memo, useMemo, useState } from "react";
import { AssistantReportView, ProductReportChart } from "./AssistantReport";
import { BusinessReportChart, BusinessReportView } from "./BusinessReport";
import { trendReport, uniqueReports } from "./reportPresentation";
import type { ChatAnswer } from "./contracts";

/** 一轮回答共用一个依据入口；同一份飞书修订在第一份引用它的报告旁标明。 */
export const AssistantEvidence = memo(function AssistantEvidence({
  result,
  question,
}: {
  result: ChatAnswer;
  question: string;
}) {
  const [open, setOpen] = useState(false);
  const reports = useMemo(() => uniqueReports(result.reports), [result.reports]);
  const chart = useMemo(
    () => (result.status === "answered" ? trendReport(reports, question) : undefined),
    [reports, question, result.status],
  );
  const shownRevisions = new Set<string>();
  if (!reports.length && !result.evidence.trim()) return null;
  return (
    <>
      {chart &&
        (chart.kind === "dashboard" ? (
          <BusinessReportChart report={chart} question={question} />
        ) : (
          <ProductReportChart report={chart} question={question} />
        ))}
      <details
        className="assistant-evidence"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>数据与规则依据</summary>
        {open && (
          <div className="assistant-evidence-content">
            {reports.map((report, index) => {
              if (report.kind !== "dashboard")
                return <AssistantReportView key={index} report={report} question={question} />;
              const isKnowledge = report.capability === "business_knowledge";
              const revision = isKnowledge
                ? report.data_version?.trim()
                : report.capability === "sales_forecast"
                  ? report.versions.forecast_sop?.trim()
                  : undefined;
              const identity = (report.scope.document_key ?? "monthly_gmv") + "/" + revision;
              const sopRevision = revision && !shownRevisions.has(identity) ? revision : undefined;
              if (sopRevision) shownRevisions.add(identity);
              return (
                <BusinessReportView
                  key={index}
                  report={report}
                  question={question}
                  sopRevision={sopRevision}
                />
              );
            })}
            {!reports.length && <p>{result.evidence}</p>}
          </div>
        )}
      </details>
    </>
  );
});
