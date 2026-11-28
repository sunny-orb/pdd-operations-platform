import { useId, useState, type ReactNode } from "react";

/** 首次只生成摘要；长表在用户展开这份报告时才进入 DOM。 */
export function EvidenceDetails({
  hasMore,
  children,
}: {
  hasMore: boolean;
  children: (expanded: boolean) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  if (!hasMore) return children(false);
  return (
    <>
      {!expanded && children(false)}
      <button
        type="button"
        className="assistant-evidence-more"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "收起明细" : "查看明细"}
      </button>
      <div id={id}>{expanded && children(true)}</div>
    </>
  );
}
