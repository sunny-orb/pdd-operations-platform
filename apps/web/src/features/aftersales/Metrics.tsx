import type { Metric } from "./contracts";
import { formatRateChange, formatReportValue } from "./format";
import { Explanation } from "./Explanation";

const riskSlots = [
  ["total_rate", "总退款率"],
  ["total_amount", "退款金额"],
  ["change_pp", "较上期变化"],
  ["target_gap", "目标差距"],
  ["excess_estimate", "超目标金额测算"],
] as const;
const summarySlots = [
  ["returned_qty", "实退数量"],
  ["inbound_amount", "实际入库金额"],
  ["gmv", "支付口径GMV"],
  ["presale_rate", "售前退款率"],
  ["aftersale_rate", "售后退款率"],
  ["total_rate", "总退款率"],
  ["records", "售后记录"],
  ["size_qty", "尺码问题"],
  ["quality_qty", "质量问题"],
] as const;

export function Metrics({
  metrics = [],
  variant,
}: {
  metrics?: Metric[];
  variant: "risk" | "summary";
}) {
  const risk = variant === "risk";
  const slots = risk ? riskSlots : summarySlots;
  return (
    <div className="m5-metric-section">
      <section
        className={risk ? "m5-metric-grid m5-risk-grid" : "m5-metric-grid"}
        aria-label={risk ? "风险五项指标" : "售后九项指标"}
      >
        {slots.map(([key, label]) => {
          const metric = metrics.find((item) => item.key === key);
          const available = metric?.value != null;
          const status = metric?.status;
          const tone =
            status === "risk"
              ? "risk"
              : status === "improved"
                ? "improved"
                : available
                  ? "reference"
                  : "pending";
          return (
            <article className={`m5-metric m5-tone-${tone}`} key={key}>
              <span>{label}</span>
              <strong>
                {key === "change_pp" && available
                  ? formatRateChange(metric.value)
                  : formatReportValue(metric?.value, metric?.type)}
              </strong>
              {!available && <small>暂无数据</small>}
              {available && tone === "risk" && (
                <small>
                  <b>风险</b>
                </small>
              )}
            </article>
          );
        })}
      </section>
      {metrics.some((metric) => metric.note || metric.detail) && (
        <Explanation label="指标口径">
          <dl>
            {slots.map(([key, label]) => {
              const metric = metrics.find((item) => item.key === key);
              const notes = [...new Set([metric?.note, metric?.detail].filter(Boolean))];
              return notes.length ? (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>
                    {notes.map((note) => (
                      <p key={note}>{note}</p>
                    ))}
                  </dd>
                </div>
              ) : null;
            })}
          </dl>
        </Explanation>
      )}
    </div>
  );
}
