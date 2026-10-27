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
  if (
    response &&
    ["pending", "running"].includes(response.computationState) &&
    !response.meta.hasCompatibleResult
  )
    return (
      <Alert
        type="info"
        showIcon
        title="结果尚未生成"
        description="本区域等待计算，不影响其他经营数据。可稍后刷新。"
      />
    );
  if (response?.computationState === "failed" && !response.meta.hasCompatibleResult)
    return (
      <Alert
        type="warning"
        showIcon
        title="分析暂不可用"
        description={response.meta.lastError?.message ?? "基础数据仍可查看，请稍后重试此区域。"}
        action={<Button onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  if (!response || response.availability === "not_connected")
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="数据尚未接入" />;
  if (!response.data)
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
      {response.computationState === "ready" && response.meta.lastAttemptStatus === "failed" && (
        <Alert
          type="warning"
          showIcon
          title="最近评分计算失败，保留上次相容结果"
          description={`${response.meta.lastAttemptAt ?? "时间未记录"}；${response.meta.lastError?.message ?? "请检查评分任务记录。"}`}
        />
      )}
      {response.computationState !== "ready" && (
        <Alert
          type="warning"
          showIcon
          title="展示上次相容结果"
          description={
            response.meta.lastError?.message ?? "新计算尚未完成，当前结果日期与版本见本区域。"
          }
        />
      )}
      {response.availability === "empty" && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="当前筛选下暂无匹配结果，可清除本区域筛选"
        />
      )}
      {response.meta.missingReasons.filter(
        (reason) => !["来源完整日尚未确认", "按当前资料归属"].includes(reason),
      ).length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="部分数据不足"
          description={
            response.meta.missingReasons
              .filter((reason) => !["来源完整日尚未确认", "按当前资料归属"].includes(reason))
              .join("；") || "完整性尚未确认，缺项不参与判断。"
          }
        />
      )}
      {response.meta.coverageNote && <span className="muted">{response.meta.coverageNote}</span>}
      {children(response.data)}
    </Space>
  );
}
