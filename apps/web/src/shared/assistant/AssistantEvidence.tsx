import { memo } from "react";
import { AssistantReportView } from "./AssistantReport";
import { BusinessReportView } from "./BusinessReport";
import { trendReport, uniqueReports } from "./reportPresentation";
import type { ChatAnswer } from "./contracts";

/** 一轮回答只提供一个来源入口；取数报告不直接等同于需要展示的卡片。 */
export const AssistantEvidence = memo(function AssistantEvidence({
  result,
  question,
}: {
  result: ChatAnswer;
  question: string;
}) {
  const reports = uniqueReports(result.reports);
  const chart = result.status === "answered" ? trendReport(reports, question) : undefined;
  if (!reports.length && !result.evidence.trim()) return null;
  return (
    <>
      {chart &&
        (chart.kind === "dashboard" ? (
          <BusinessReportView report={chart} showChart mode="chart" question={question} />
        ) : (
          <AssistantReportView report={chart} question={question} mode="chart" />
        ))}
      <details className="assistant-evidence">
        <summary>数据与规则依据</summary>
        <div className="assistant-evidence-content">
          {reports.map((report, index) =>
            report.kind === "dashboard" ? (
              <BusinessReportView key={index} report={report} showChart={false} mode="evidence" />
            ) : (
              <AssistantReportView
                key={index}
                report={report}
                question={question}
                showChart={false}
                mode="evidence"
              />
            ),
          )}
          {!reports.length && <p>{result.evidence}</p>}
        </div>
      </details>
    </>
  );
});
