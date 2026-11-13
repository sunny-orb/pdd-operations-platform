import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import type { Metric } from "./contracts";
import { formatRateChange, formatReportValue } from "./format";

const riskSlots = [
  ["total_rate", "总退款率"],
  ["total_amount", "退款金额"],
  ["change_pp", "较上期变化"],
  ["target_gap", "目标差距"],
  ["excess_estimate", "预计损失"],
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
    <section
      className={risk ? "m5-metric-grid m5-risk-grid" : "m5-metric-grid"}
      aria-label={risk ? "风险五项指标" : "售后九项指标"}
    >
      {slots.map(([key, label]) => {
        const metric = metrics.find((item) => item.key === key);
        const available = metric?.value != null;
        const status = metric?.status;
        const tone = status === "risk" ? "risk" : status === "improved" ? "improved" : "pending";
        const detail = risk ? metric?.detail || metric?.note : metric?.detail;
        return (
          <article className={`m5-metric m5-tone-${tone}`} key={key}>
            <span>
              {label}
              {!risk && metric?.note && (
                <Tooltip title={metric.note} trigger={["hover", "focus"]}>
                  <button type="button" className="m5-metric-help" aria-label={`${label}口径`}>
                    <InfoCircleOutlined />
                  </button>
                </Tooltip>
              )}
            </span>
            <strong>
              {key === "change_pp" && available
                ? formatRateChange(metric.value)
                : formatReportValue(metric?.value, metric?.type)}
            </strong>
            {(risk || detail || !available) && (
              <small>
                {risk && (
                  <b>{tone === "risk" ? "风险" : tone === "improved" ? "已改善" : "待验证"} </b>
                )}
                {detail || (!available && (risk ? "暂无可用数据" : "待补充"))}
              </small>
            )}
          </article>
        );
      })}
    </section>
  );
}
