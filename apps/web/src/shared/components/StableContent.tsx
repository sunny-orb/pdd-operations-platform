import { memo, useEffect, useState, type ReactNode } from "react";
import { Skeleton } from "antd";
import "./stable-content.css";

type ContentProps = { pending: boolean; children: ReactNode };

// 切换时保留上一份DOM和尺寸，旧结果标记后仅作为过渡背景。
const RetainedContent = memo(
  function RetainedContent({ children }: ContentProps) {
    return children;
  },
  (_, next) => next.pending,
);

export function StableContent({
  pending,
  children,
  label,
  ready,
}: ContentProps & { label: string; ready: boolean }) {
  const [hasShown, setHasShown] = useState(false);
  if (!pending && ready && !hasShown) setHasShown(true);
  const [indicating, setIndicating] = useState(false);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setIndicating(true), 160);
    return () => {
      window.clearTimeout(timer);
      setIndicating(false);
    };
  }, [pending]);
  return (
    <div
      className="stable-content"
      data-retaining={(pending && hasShown) || undefined}
      data-indicating={(pending && indicating) || undefined}
    >
      <div
        className="stable-content-body"
        aria-busy={pending}
        aria-hidden={pending || undefined}
        inert={pending || undefined}
      >
        <RetainedContent pending={pending}>{children}</RetainedContent>
      </div>
      {pending && indicating && (
        <div className="stable-content-indicator" role="status">
          {hasShown ? (
            `${label}  上次结果`
          ) : (
            <>
              <span className="stable-content-label">{label}</span>
              <Skeleton active paragraph={{ rows: 3 }} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
