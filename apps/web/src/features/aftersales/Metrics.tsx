import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import type { Metric } from "./contracts";
import { formatValue } from "./format";
export function Metrics({ metrics, label }: { metrics: Metric[]; label: string }) {
  return (
    <section className="m5-metric-grid" aria-label={label}>
      {metrics.map((metric) => (
        <article className="m5-metric" key={metric.key}>
          <span>
            {metric.label}{" "}
            {metric.note && (
              <Tooltip title={metric.note}>
                <InfoCircleOutlined aria-label={`${metric.label}口径`} />
              </Tooltip>
            )}
          </span>
          <strong>{formatValue(metric.value, metric.type)}</strong>
          <small>{metric.detail}</small>
        </article>
      ))}
    </section>
  );
}
