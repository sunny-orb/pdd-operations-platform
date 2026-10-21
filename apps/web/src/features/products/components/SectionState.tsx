import { Alert, Button, Empty, Skeleton, Space } from "antd";
import { useContext, type ReactNode } from "react";
import { ProductBootstrapContext } from "../api/queries";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Envelope } from "../api/contracts";

export type QueryView<T> = UseQueryResult<Envelope<T>, Error>;
/** 单区块隔离失败与缺数；不把HTTP成功等同数据完整，不自动补零。 */
export function SectionState<T>({
  query,
  children,
}: {
  query: QueryView<T>;
  children: (data: T) => ReactNode;
  onVersionConflict?: () => void;
}) {
  const bootstrap = useContext(ProductBootstrapContext);
  if (bootstrap?.error)
    return (
      <Alert
        type="error"
        showIcon
        title="页面数据范围加载失败"
        description={bootstrap.error.message}
        action={
          <Button size="small" onClick={bootstrap.retry}>
            重试来源信息
          </Button>
        }
      />
    );
  if (bootstrap?.notConnected)
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="数据尚未接入" />;
  if (query.isPending) return <Skeleton active paragraph={{ rows: 3 }} title={false} />;
  if (query.isError)
    return (
      <Alert
        type="error"
        showIcon
        title="此区域暂时无法加载"
        description={query.error.message}
        action={
          <Button size="small" onClick={() => void query.refetch()}>
            重试
          </Button>
        }
      />
    );
  const response = query.data;
  if (!response || response.availability === "not_connected")
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="数据尚未接入" />;
  if (response.computationState === "failed")
    return (
      <Alert
        type="warning"
        showIcon
        title="分析暂不可用"
        description="基础数据仍可查看，请稍后重试此区域。"
        action={<Button onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  if (response.computationState === "pending")
    return (
      <Alert
        type="info"
        showIcon
        title="结果尚未生成"
        description="本区域等待计算，不影响其他经营数据。可稍后刷新。"
      />
    );
  if (response.availability === "empty" || !response.data)
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="当前筛选下暂无匹配结果，可清除本区域筛选"
      />
    );
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      {query.isFetching && (
        <span className="muted" role="status">
          正在更新当前范围…
        </span>
      )}
      {response.meta.completeness !== "complete" && (
        <Alert
          type="warning"
          showIcon
          title="部分数据不足"
          description={
            response.meta.missingReasons.join("；") || "完整性尚未确认，缺项不参与判断。"
          }
        />
      )}
      {children(response.data)}
    </Space>
  );
}
