import { memo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { decimalParts, formatFixedDecimal } from "../format/decimal";
import type { BusinessReport } from "./contracts";

type Unit = BusinessReport["facts"][number]["unit"];
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

/** 各域只负责输出事实和表格，统一展示保留NULL、单位、范围、分页及证据。 */
export const BusinessReportView = memo(function BusinessReportView({
  report,
  showChart,
}: {
  report: BusinessReport;
  showChart: boolean;
}) {
  const [selected, setSelected] = useState("");
  const series = report.series.find((item) => item.key === selected) ?? report.series[0];
  return (
    <section className="assistant-report" aria-label={report.title + "数据"}>
      <header>
        <strong>{report.title}</strong>
        {report.period && (
          <small>
            {report.period.date_from} 至 {report.period.date_to}
          </small>
        )}
      </header>
      {showChart && series && (
        <>
          <label className="assistant-chart-heading">
            每日趋势
            <select
              aria-label="趋势指标"
              value={series.key}
              onChange={(event) => setSelected(event.target.value)}
            >
              {report.series.map((item) => (
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
              <LineChart
                data={series.points.map((point) => ({
                  date: point.date,
                  raw: point.value,
                  value:
                    point.value == null
                      ? null
                      : Number(point.value) * (series.unit === "percent" ? 100 : 1),
                }))}
              >
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
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
      <details>
        <summary>查看指标与数据依据</summary>
        {report.facts.length > 0 && (
          <table>
            <tbody>
              {report.facts.map((item, index) => (
                <tr key={item.key + index}>
                  <th>
                    {item.label}
                    {item.reason && <small>{item.reason}</small>}
                  </th>
                  <td>{display(item.value, item.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {report.tables.map((table, index) => (
          <div className="assistant-data-table" key={index}>
            <table>
              <caption>{table.title}</caption>
              <thead>
                <tr>
                  {table.columns
                    .filter((column) => column.key !== "listing_id")
                    .map((column) => (
                      <th key={column.key}>{column.label}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, i) => (
                  <tr key={i}>
                    {table.columns
                      .filter((column) => column.key !== "listing_id")
                      .map((column) => (
                        <td key={column.key}>{display(row[column.key], column.unit)}</td>
                      ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {table.total != null && (
              <small>
                {table.rows.length
                  ? "显示第" + (table.offset + 1) + "—" + (table.offset + table.rows.length) + "条"
                  : "当前没有匹配记录"}
                ，共{table.total}条
              </small>
            )}
          </div>
        ))}
        <details>
          <summary>来源与口径</summary>
          <p>{report.source}</p>
          {report.notes.map((note, i) => (
            <p key={i}>{note}</p>
          ))}
          <small>数据版本 {report.data_version ?? "当前状态"}</small>
        </details>
      </details>
    </section>
  );
});
