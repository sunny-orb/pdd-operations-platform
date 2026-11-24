import { useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatFixedDecimal } from "../format/decimal";
import type { AssistantReport } from "./contracts";
import { hasTrend, questionMetric, trendPoints, type ReportMode } from "./reportPresentation";

type Unit = AssistantReport["metrics"][number]["unit"];
function amount(value: string | null | undefined, unit: Unit) {
  const n = formatFixedDecimal(value, unit === "number" ? 0 : 2, unit === "percent" ? 2 : 0, true);
  if (n === null) return "—";
  if (unit === "currency") return `¥${n}`;
  if (unit === "percent") return `${n}%`;
  if (unit === "ratio") return `${n}×`;
  return n;
}

const ruleStates: Record<string, string> = {
  evaluated: "已评估",
  rated: "已评估",
  empty: "本期无经营记录",
  not_applicable: "此商品不适用",
  insufficient_data: "数据不足",
  partial: "仅部分规则可评估",
  unrated: "尚不能评级",
  insufficient_evidence: "缺少推广数据",
};
const metricStates: Record<string, string> = {
  partial: "部分记录可用",
  no_records: "没有记录",
  invalid_values: "指标字段不足",
  zero_denominator: "分母为零",
  zero_baseline: "前期为零",
  history_not_received: "前期超出已接收范围",
  missing: "缺少指标数据",
};
function RuleEvidence({ rules }: { rules: NonNullable<AssistantReport["evidence"]["rules"]> }) {
  const evaluated = rules.status === "rated" || rules.status === "evaluated";
  return (
    <section className="assistant-rule-evidence" aria-label="规则依据">
      <strong>规则评估 · {ruleStates[rules.status] ?? "查看评估结果"}</strong>
      {rules.date_to && (
        <p>
          {rules.date_from ? "评估期 " + rules.date_from + " 至 " : "评估日 "}
          {rules.date_to}
        </p>
      )}
      {rules.baseline_from && rules.baseline_to && (
        <p>
          基准期 {rules.baseline_from} 至 {rules.baseline_to}
        </p>
      )}
      {rules.ranking_basis && <p>{rules.ranking_basis}</p>}
      {evaluated && !rules.findings.length && <p>本次评估未触发已配置规则。</p>}
      {rules.findings.map((f, i) => (
        <div key={i}>
          <b>{f.label ?? f.rule}</b>
          <p>{f.basis ?? f.evidence}</p>
          {f.action && <p>{f.action}</p>}
        </div>
      ))}
      {rules.checks
        ?.filter(
          (check) =>
            check.reason && (check.state === "unavailable" || check.state === "not_applicable"),
        )
        .map((check) => (
          <p key={check.key}>
            {check.label} · {check.reason}
          </p>
        ))}
      <small>规则版本 {rules.version}</small>
    </section>
  );
}
/** 数值与单位取自本次查询。仅曲线坐标转为浮点数，明细与提示按原十进制值展示。 */
export function AssistantReportView({
  report,
  question,
  showChart = true,
  mode = "default",
}: {
  report: AssistantReport;
  question: string;
  showChart?: boolean;
  mode?: ReportMode;
}) {
  const { period, coverage, metrics, series } = report;
  const available = metrics.filter((m) =>
    hasTrend(series.map((day) => ({ date: day.date, value: day.values[m.key] }))),
  );
  const [choice, setChoice] = useState("");
  const selected = available.find((m) => m.key === choice) ?? questionMetric(available, question);
  const points = selected
    ? trendPoints(
        series.map(({ date, values }) => ({ date, value: values[selected.key] })),
        selected.unit === "percent",
      )
    : [];
  const label = report.kind === "product_performance" ? "商品经营" : "商品推广";
  const Details = mode === "evidence" ? "div" : "details";
  if (mode === "chart" && (!selected || !showChart)) return null;
  return (
    <section
      className={
        mode === "default"
          ? "assistant-report"
          : mode === "chart"
            ? "assistant-trend"
            : "assistant-evidence-report"
      }
      aria-label={label + "数据"}
    >
      <header>
        <strong>{label}</strong>
        <small>
          {period.date_from} 至 {period.date_to}
        </small>
      </header>
      {showChart && selected && points.length > 1 && (
        <>
          <div className="assistant-chart-heading">
            <label>
              每日趋势
              <select
                aria-label="趋势指标"
                value={selected.key}
                onChange={(event) => setChoice(event.target.value)}
              >
                {available.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            <small>
              {{ currency: "元", percent: "%", ratio: "倍", number: "" }[selected.unit]}
            </small>
          </div>
          <div role="img" aria-label={selected.label + "每日趋势，数值见下方明细"}>
            <ResponsiveContainer
              width="100%"
              height={112}
              initialDimension={{ width: 280, height: 112 }}
            >
              <LineChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tickFormatter={(v: string) => v.slice(5)}
                  tick={{ fontSize: 10 }}
                  minTickGap={28}
                />
                <YAxis width="auto" tick={{ fontSize: 10 }} tickCount={3} />
                <Tooltip
                  formatter={(_v, _n, entry) => [
                    amount(entry.payload.raw, selected.unit),
                    selected.label,
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
        </>
      )}
      {mode !== "chart" && (
        <Details>
          {mode === "default" && <summary>查看指标与数据依据</summary>}
          <p>
            对比期间 {period.previous_from} 至 {period.previous_to}
          </p>
          <div className="assistant-data-table">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>本期</th>
                  <th>前期</th>
                  <th>变化率</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((m) => (
                  <tr key={m.key}>
                    <th>
                      {m.label}
                      {(m.reason || metricStates[m.status]) && (
                        <small>{m.reason ?? metricStates[m.status]}</small>
                      )}
                    </th>
                    <td>{amount(m.current, m.unit)}</td>
                    <td>{amount(m.previous, m.unit)}</td>
                    <td>{amount(m.change, "percent")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            本期有记录 {coverage.current.observed_days}/{coverage.current.expected_days} 天，前期{" "}
            {coverage.previous.observed_days}/{coverage.previous.expected_days} 天。
          </p>
          {coverage.current.missing_dates.length > 0 && (
            <p>本期无记录日期 {coverage.current.missing_dates.join("、")}</p>
          )}
          {coverage.previous.missing_dates.length > 0 && (
            <p>前期无记录日期 {coverage.previous.missing_dates.join("、")}</p>
          )}
          <p>
            完整截至日 {coverage.complete_through ?? "尚未确认"}
            <br />
            来源 {report.evidence.source}
            {!report.versions && (
              <>
                <br />
                数据版本 {report.data_version}
              </>
            )}
          </p>
          <Details>
            {mode === "default" && <summary>来源与规则详情</summary>}
            {report.versions && (
              <p>
                商品版本 {report.versions.product_version}
                <br />
                推广版本 {report.versions.promotion_version}
              </p>
            )}
            {report.evidence.rules && <RuleEvidence rules={report.evidence.rules} />}
          </Details>
          {(mode === "evidence" ? series.length > 0 && metrics.length > 0 : !!selected) && (
            <Details>
              {mode === "default" && <summary>逐日明细</summary>}
              <div className="assistant-data-table">
                <table>
                  {mode === "evidence" && <caption>逐日明细</caption>}
                  <thead>
                    <tr>
                      <th>日期</th>
                      {(mode === "evidence" ? metrics : [selected!]).map((metric) => (
                        <th key={metric.key}>{metric.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {series.map((day) => (
                      <tr key={day.date}>
                        <th>{day.date}</th>
                        {(mode === "evidence" ? metrics : [selected!]).map((metric) => (
                          <td key={metric.key}>{amount(day.values[metric.key], metric.unit)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Details>
          )}
        </Details>
      )}
    </section>
  );
}
