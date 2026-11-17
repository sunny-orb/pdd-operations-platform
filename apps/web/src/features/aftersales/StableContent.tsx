import { memo, type ReactNode } from "react";
import { Spin } from "antd";

type ContentProps = { pending: boolean; children: ReactNode };

// 请求期间只保留上一份DOM的尺寸与组件状态；外层隐藏内容并禁止交互，不展示旧指标。
const RetainedContent = memo(
  function RetainedContent({ children }: ContentProps) {
    return children;
  },
  (_, next) => next.pending,
);

export function StableContent({ pending, children, label }: ContentProps & { label: string }) {
  return (
    <div className="m5-stable-content">
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
          <Spin size="small" />
          {label}
        </div>
      )}
    </div>
  );
}
