import type { ReactNode } from "react";
import { Card } from "antd";

/** 四张经营摘要卡共用排版；数值和状态由中心传入，不在组件内计算指标。 */
export function SummaryCard({
  label,
  value,
  unit,
  description,
  tone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  description: ReactNode;
  tone: "green" | "amber" | "blue" | "purple";
}) {
  return (
    <Card className={`m6-summary-card m6-summary-card--${tone}`}>
      <span className="m6-summary-label">{label}</span>
      <b className="m6-summary-value">
        {value}
        {unit && (
          <>
            {" "}
            <em>{unit}</em>
          </>
        )}
      </b>
      <small className="m6-summary-description">{description}</small>
    </Card>
  );
}
