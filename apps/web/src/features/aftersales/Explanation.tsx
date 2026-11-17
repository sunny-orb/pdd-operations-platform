import type { ReactNode } from "react";

/** 本页说明按需展开，经营数值和异常状态留在原位置。 */
export function Explanation({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="m5-explanation" aria-label={label}>
      <summary>{label}</summary>
      <div>{children}</div>
    </details>
  );
}
