import { useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatFixedDecimal } from "../format/decimal";
import type { AssistantReport } from "./contracts";
import { EvidenceDetails } from "./EvidenceDetails";
import {
  hasTrend,
  longEvidence,
  previewMetrics,
  questionMetric,
  shortEvidence,
  trendPoints,
} from "./reportPresentation";

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
type Metric = AssistantReport["metrics"][number];
type Rules = NonNullable<AssistantReport["evidence"]["rules"]>;
type Check = NonNullable<Rules["checks"]>[number];

function needsCheckNote(check: Check) {
  return check.reason && (check.state === "unavailable" || check.state === "not_applicable");
}

function hasRuleDetails(rules: Rules) {
  return Boolean(
    rules.date_to ||
    rules.ranking_basis ||
    (rules.baseline_from && rules.baseline_to) ||
    rules.findings.length > 1 ||
    rules.findings.some((f) => f.action || longEvidence(f.basis ?? f.evidence)) ||
    rules.checks?.some(needsCheckNote),
  );
}

function reportLabel(report: AssistantReport) {
  return report.kind === "product_performance" ? "商品经营" : "商品推广";
}

export function ProductReportChart({
  report,
  question,
}: {
  report: AssistantReport;
  question: string;
}) {
  const { metrics, series, period } = report;
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
  if (!selected || points.length < 2) return null;
  const label = reportLabel(report);
  return (
    <section className="assistant-trend" aria-label={label + "数据"}>
      <header>
        <strong>{label}</strong>
        <small>
          {period.date_from} 至 {period.date_to}
        </small>
      </header>
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
        <small>{{ currency: "元", percent: "%", ratio: "倍", number: "" }[selected.unit]}</small>
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
    </section>
  );
}

function RuleEvidence({ rules, expanded }: { rules: Rules; expanded: boolean }) {
  const evaluated = rules.status === "rated" || rules.status === "evaluated";
  const findings = expanded ? rules.findings : rules.findings.slice(0, 1);
  return (
    <section className="assistant-rule-evidence" aria-label="规则依据">
      <strong>规则评估 · {ruleStates[rules.status] ?? "查看评估结果"}</strong>
      {evaluated && !rules.findings.length && <p>本次评估未触发已配置规则。</p>}
      {findings.map((f, i) => {
        const basis = f.basis ?? f.evidence;
        return (
          <div key={i}>
            <b>{f.label ?? f.rule}</b>
            {basis && <p>{expanded ? basis : shortEvidence(basis)}</p>}
            {expanded && f.action && <p>{f.action}</p>}
          </div>
        );
      })}
      {expanded && (
        <>
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
          {rules.checks?.filter(needsCheckNote).map((check) => (
            <p key={check.key}>
              {check.label} · {check.reason}
            </p>
          ))}
          <small>规则版本 {rules.version}</small>
        </>
      )}
    </section>
  );
}

function MetricTable({ metrics, compact }: { metrics: Metric[]; compact: boolean }) {
  if (!metrics.length) return null;
  return (
    <div className="assistant-data-table">
      <table aria-label="商品指标">
        <thead>
          <tr>
            <th>指标</th>
            <th>本期</th>
            <th>前期</th>
            <th>变化率</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => {
            const reason = metric.reason ?? metricStates[metric.status];
            return (
              <tr key={metric.key}>
                <th scope="row">
                  {metric.label}
                  {reason && <small>{compact ? shortEvidence(reason) : reason}</small>}
                </th>
                <td>{amount(metric.current, metric.unit)}</td>
                <td>{amount(metric.previous, metric.unit)}</td>
                <td>{amount(metric.change, "percent")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProductDailyTable({ report }: { report: AssistantReport }) {
  return (
    <div className="assistant-data-table">
      <table>
        <caption>逐日明细</caption>
        <thead>
          <tr>
            <th>日期</th>
            {report.metrics.map((m) => (
              <th key={m.key}>{m.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {report.series.map((day) => (
            <tr key={day.date}>
              <th scope="row">{day.date}</th>
              {report.metrics.map((m) => (
                <td key={m.key}>{amount(day.values[m.key], m.unit)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 同名跨店商品按报告自身的商品与店铺展示。覆盖只统计实际有记录的天数。 */
export function AssistantReportView({
  report,
  question,
}: {
  report: AssistantReport;
  question: string;
}) {
  const { coverage, metrics, period, product } = report;
  const rules = report.evidence.rules;
  const label = reportLabel(report);
  const leadMetrics = previewMetrics(metrics, question);
  const daily = report.series.length > 0 && metrics.length > 0;
  const hasMore =
    daily ||
    metrics.length > leadMetrics.length ||
    metrics.some((m) => longEvidence(m.reason)) ||
    coverage.current.missing_dates.length > 0 ||
    coverage.previous.missing_dates.length > 0 ||
    Boolean(rules && hasRuleDetails(rules));
  return (
    <section className="assistant-evidence-report" aria-label={label + "数据"}>
      <header>
        <strong>
          {product.name || product.product_id || product.listing_id} ·{" "}
          {product.shop_name || product.shop_id}
        </strong>
        <small>{label}</small>
      </header>
      <p className="assistant-evidence-meta">
        来源 <span>{report.evidence.source}</span>
      </p>
      <p className="assistant-evidence-meta">
        本期 {period.date_from} 至 {period.date_to} · 有记录 {coverage.current.observed_days}/
        {coverage.current.expected_days} 天
      </p>
      <p className="assistant-evidence-meta">
        前期 {period.previous_from} 至 {period.previous_to} · 有记录{" "}
        {coverage.previous.observed_days}/{coverage.previous.expected_days} 天
      </p>
      <p className="assistant-evidence-meta">
        完整截至日 {coverage.complete_through ?? "尚未确认"}
      </p>
      <EvidenceDetails hasMore={hasMore}>
        {(expanded) => (
          <>
            {rules && <RuleEvidence rules={rules} expanded={expanded} />}
            <MetricTable metrics={expanded ? metrics : leadMetrics} compact={!expanded} />
            {expanded && (
              <>
                {coverage.current.missing_dates.length > 0 && (
                  <p>本期无记录日期 {coverage.current.missing_dates.join("、")}</p>
                )}
                {coverage.previous.missing_dates.length > 0 && (
                  <p>前期无记录日期 {coverage.previous.missing_dates.join("、")}</p>
                )}
                {daily && <ProductDailyTable report={report} />}
              </>
            )}
          </>
        )}
      </EvidenceDetails>
    </section>
  );
}
