import { getJson } from "./client";

type SystemStatus = {
  databaseConfigured: boolean;
  queriedAt: string;
};

/** 读取并校验骨架状态；输入取消信号，返回连接配置与查询时间，不代表业务就绪。 */
export async function getSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  const result = await getJson("/api/v1/system/status", signal);
  if (typeof result !== "object" || result === null || !("data" in result) || !("meta" in result)) {
    throw new Error("接口返回格式不符合约定。");
  }
  const { data, meta } = result;
  if (
    typeof data !== "object" ||
    data === null ||
    !("database_configured" in data) ||
    !("business_data" in data) ||
    data.business_data !== "not_integrated" ||
    typeof data.database_configured !== "boolean" ||
    typeof meta !== "object" ||
    meta === null ||
    !("queried_at" in meta) ||
    typeof meta.queried_at !== "string" ||
    !Number.isFinite(Date.parse(meta.queried_at))
  )
    throw new Error("接口返回格式不符合约定。");
  return { databaseConfigured: data.database_configured, queriedAt: meta.queried_at };
}
