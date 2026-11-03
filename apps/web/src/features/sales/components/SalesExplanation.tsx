import { memo, type ReactNode } from "react";
import "./sales-explanation.css";

/** 店铺销售页的简短说明共用无边框折叠；内容和数据由各业务区块提供。 */
export const SalesExplanation = memo(function SalesExplanation({
  title,
  children,
  className = "",
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={`sales-explanation ${className}`.trim()}>
      <summary>{title}</summary>
      {children}
    </details>
  );
});
