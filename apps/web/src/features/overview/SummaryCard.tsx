import { memo, type ReactNode } from "react";
import { Card } from "antd";

/** 四张经营摘要卡共用排版；数值和状态由中心传入，不在组件内计算指标。 */
export const SummaryCard = memo(function SummaryCard({
  label,
  value,
  unit,
  description,
  tone,
  href,
  onNavigate,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  description: ReactNode;
  tone: "green" | "amber" | "blue" | "purple";
  href?: string;
  onNavigate?: () => void;
}) {
  const main = (
    <>
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
    </>
  );
  return (
    <Card className={`m6-summary-card m6-summary-card--${tone}`}>
      {href ? (
        <a className="m6-summary-main" href={href} onClick={onNavigate}>
          {main}
        </a>
      ) : (
        main
      )}
      <small className="m6-summary-description">{description}</small>
    </Card>
  );
});
