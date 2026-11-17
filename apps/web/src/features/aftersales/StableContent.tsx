import { memo, useState, type ReactNode } from "react";
import { Skeleton } from "antd";

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
  return (
    <div className="m5-stable-content" data-retaining={(pending && hasShown) || undefined}>
      <div
        className="m5-retained-content"
        aria-busy={pending}
        aria-hidden={pending || undefined}
        inert={pending || undefined}
      >
        <RetainedContent pending={pending}>{children}</RetainedContent>
      </div>
      {pending && (
        <div className="m5-loading-indicator" role="status">
          {hasShown ? (
            `${label}  上次结果`
          ) : (
            <>
              <span className="m5-loading-label">{label}</span>
              <Skeleton active paragraph={{ rows: 3 }} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
