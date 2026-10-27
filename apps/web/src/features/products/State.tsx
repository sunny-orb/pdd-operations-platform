import { Alert, Button, Spin } from "antd";
import { ApiError } from "./api";
export function State({
  pending,
  error,
  retry,
}: {
  pending: boolean;
  error: unknown;
  retry: () => void;
}) {
  if (error)
    return (
      <Alert
        type="error"
        showIcon
        title={
          error instanceof ApiError && error.status === 409
            ? "数据版本已变化，请重试"
            : "本区数据暂不可用"
        }
        description={error instanceof ApiError ? error.code : "响应或连接失败，未替换为零值"}
        action={
          <Button size="small" onClick={retry}>
            重试
          </Button>
        }
      />
    );
  if (pending)
    return (
      <div className="m1-loading" role="status">
        <Spin size="small" /> 正在读取本地已发布数据…
      </div>
    );
  return null;
}
