import { memo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { decimalParts, formatFixedDecimal } from "../format/decimal";
import type { BusinessReport } from "./contracts";
import { EvidenceDetails } from "./EvidenceDetails";
import {
  businessScope,
  domainNames,
  hasTrend,
  longEvidence,
  previewMetrics,
  questionMetric,
  shortEvidence,
  trendPoints,
} from "./reportPresentation";

type Unit = BusinessReport["facts"][number]["unit"];
type ReportTable = BusinessReport["tables"][number];

function display(value: unknown, unit: Unit) {
  if (value == null) return "—";
  if (unit === "text") return String(value);
  const digits = unit === "number" ? Math.max(decimalParts(String(value))?.scale ?? 0, 0) : 2;
  let result = formatFixedDecimal(String(value), digits, unit === "percent" ? 2 : 0, true);
  if (result === null) return "—";
  if (unit === "number" && digits) result = result.replace(/\.?0+$/, "");
  return unit === "currency"
    ? "¥" + result
    : unit === "percent"
      ? result + "%"
      : unit === "ratio"
        ? result + "×"
        : result;
}

function periodLabel(period: BusinessReport["period"]) {
  if (!period) return null;
  return period.date_from === period.date_to
    ? period.date_to
    : period.date_from + " 至 " + period.date_to;
}

export function BusinessReportChart({
  report,
  question,
}: {
  report: BusinessReport;
  question: string;
}) {
  const [selected, setSelected] = useState("");
  const available = report.series.filter((item) => item.unit !== "text" && hasTrend(item.points));
  const series =
    available.find((item) => item.key === selected) ?? questionMetric(available, question);
  if (!series) return null;
  return (
    <section className="assistant-trend" aria-label={report.title + "数据"}>
      <header>
        <strong>{report.title}</strong>
        {report.period && <small>{periodLabel(report.period)}</small>}
      </header>
      <label className="assistant-chart-heading">
        每日趋势
        <select
          aria-label="趋势指标"
          value={series.key}
          onChange={(event) => setSelected(event.target.value)}
        >
          {available.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <div role="img" aria-label={series.label + "趋势"}>
        <ResponsiveContainer
          width="100%"
          height={130}
          initialDimension={{ width: 280, height: 130 }}
        >
          <LineChart data={trendPoints(series.points, series.unit === "percent")}>
            <XAxis
              dataKey="date"
              tickFormatter={(value: string) => value.slice(5)}
              tick={{ fontSize: 10 }}
              minTickGap={28}
            />
            <YAxis width="auto" tick={{ fontSize: 10 }} />
            <Tooltip
              formatter={(_value, _name, entry) => [
                display(entry.payload.raw, series.unit),
                series.label,
              ]}
            />
            <Line
              type="linear"
              dataKey="value"
              stroke="#2167b9"
              strokeWidth={2}
              dot={{ r: 2, fill: "#2167b9" }}
              connectNulls={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function FactList({ facts, compact }: { facts: BusinessReport["facts"]; compact: boolean }) {
  if (!facts.length) return null;
  return (
    <div className="assistant-data-table">
      <table aria-label="指标">
        <tbody>
          {facts.map((item, index) => (
            <tr key={item.key + index}>
              <th scope="row">
                {item.label}
                {item.reason && <small>{compact ? shortEvidence(item.reason) : item.reason}</small>}
              </th>
              <td>
                {compact && item.unit === "text"
                  ? shortEvidence(display(item.value, "text"))
                  : display(item.value, item.unit)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const previewRowLimit = 2;

function previewColumns(table: ReportTable) {
  const readable = table.columns.filter(({ key }) => {
    if (key === "shop_id" || key === "product_id")
      return !table.columns.some((col) => col.key === key.replace("_id", "_name"));
    return !/^(id|code|domain|rule_version|data_version)$|_id$/.test(key);
  });
  const columns = readable.slice(0, 3);
  // 单列给出的数值单位与业务日期和摘要一起显示。
  for (const column of readable) {
    if ((column.key === "unit" || column.key === "date") && !columns.includes(column))
      columns.push(column);
  }
  return columns;
}

function TableView({ table, compact }: { table: ReportTable; compact: boolean }) {
  const rows = compact ? table.rows.slice(0, previewRowLimit) : table.rows;
  const columns = compact
    ? previewColumns(table)
    : table.columns.filter((col) => col.key !== "listing_id");
  return (
    <div className="assistant-data-table">
      <table>
        <caption>{table.title}</caption>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((col) => {
                const value = display(row[col.key], col.unit);
                return (
                  <td key={col.key}>
                    {compact && col.unit === "text" ? shortEvidence(value) : value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {table.total != null && (
        <small>
          {rows.length
            ? "显示第" + (table.offset + 1) + "—" + (table.offset + rows.length) + "条"
            : "当前没有匹配记录"}
          ，共{table.total}条
        </small>
      )}
    </div>
  );
}

function hasTableDetails(table: ReportTable) {
  const columns = previewColumns(table);
  return (
    table.rows.length > previewRowLimit ||
    columns.length < table.columns.filter((col) => col.key !== "listing_id").length ||
    table.rows.some((row) =>
      columns.some((col) => col.unit === "text" && longEvidence(row[col.key])),
    )
  );
}

function DailyTable({ series }: { series: BusinessReport["series"][number] }) {
  return (
    <div className="assistant-data-table">
      <table>
        <caption>{series.label}逐日明细</caption>
        <thead>
          <tr>
            <th scope="col">日期</th>
            <th scope="col">{series.label}</th>
          </tr>
        </thead>
        <tbody>
          {series.points.map((point, index) => (
            <tr key={index}>
              <th scope="row">{point.date}</th>
              <td>{display(point.value, series.unit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 主层直接列出报告实际来源、期间与关键指标；表格分页和逐日值都来自原报告。 */
export const BusinessReportView = memo(function BusinessReportView({
  report,
  question,
  sopRevision,
}: {
  report: BusinessReport;
  question: string;
  sopRevision?: string;
}) {
  const { facts, tables, series } = report;
  const scope = businessScope(report);
  const notes = [...new Set(report.notes)].filter(
    (note) => note.trim() && note !== report.source && note !== report.title,
  );
  const isForecast = report.capability === "sales_forecast";
  const sourceUrl =
    report.capability === "business_knowledge" &&
    typeof report.scope.document_url === "string" &&
    /^https:\/\/kocotree\.feishu\.cn\/docx\/[a-zA-Z0-9]+$/.test(report.scope.document_url)
      ? report.scope.document_url
      : undefined;
  // 预测公式可分两条原句（中心值、加权日均），不在界面解析或改写公式。
  const leadNotes = isForecast
    ? notes.slice(0, 2).map(shortEvidence)
    : notes.length <= 2 && !longEvidence(notes.join(""))
      ? notes
      : notes.slice(0, 1).map(shortEvidence);
  const leadFacts = previewMetrics(facts, question);
  const leadTable = facts.length ? undefined : tables[0];
  const hasMore =
    facts.length > leadFacts.length ||
    leadFacts.some(
      (item) => longEvidence(item.reason) || (item.unit === "text" && longEvidence(item.value)),
    ) ||
    notes.length > leadNotes.length ||
    leadNotes.some((note, i) => note !== notes[i]) ||
    tables.length > (leadTable ? 1 : 0) ||
    (leadTable && hasTableDetails(leadTable)) ||
    series.some((item) => item.points.length > 0);
  return (
    <section className="assistant-evidence-report" aria-label={report.title + "数据"}>
      <header>
        <strong>{report.title}</strong>
        {domainNames[report.domain] &&
          domainNames[report.domain] !== report.title &&
          !["business_knowledge", "sales_forecast"].includes(report.capability) && (
            <small>{domainNames[report.domain]}</small>
          )}
      </header>
      <p className="assistant-evidence-meta">
        来源{" "}
        {sourceUrl ? (
          <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
            {report.source}
          </a>
        ) : (
          <span>{report.source}</span>
        )}
      </p>
      {report.period && (
        <p className="assistant-evidence-meta">
          数据期间 <span>{periodLabel(report.period)}</span>
        </p>
      )}
      {scope && (
        <p className="assistant-evidence-meta">
          范围 <span>{scope}</span>
        </p>
      )}
      {sopRevision && <p className="assistant-evidence-meta">飞书修订号 {sopRevision}</p>}
      <EvidenceDetails hasMore={Boolean(hasMore)}>
        {(expanded) => {
          const noteList = (expanded ? notes : leadNotes).map((note, i) => (
            <p className="assistant-evidence-note" key={i}>
              {note}
            </p>
          ));
          return (
            <>
              {isForecast && noteList}
              <FactList facts={expanded ? facts : leadFacts} compact={!expanded} />
              {expanded
                ? tables.map((table, i) => <TableView table={table} key={i} compact={false} />)
                : leadTable && <TableView table={leadTable} compact />}
              {expanded &&
                series
                  .filter((item) => item.points.length > 0)
                  .map((item, i) => <DailyTable key={i} series={item} />)}
              {!isForecast && noteList}
            </>
          );
        }}
      </EvidenceDetails>
    </section>
  );
});
