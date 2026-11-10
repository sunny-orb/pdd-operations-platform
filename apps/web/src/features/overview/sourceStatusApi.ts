import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { getJson } from "../../shared/api/client";

const text = z.string().nullable();
const dataset = z.object({
  code: z.string(),
  name: z.string(),
  version_id: text,
  date: text,
  received_at: text,
  last_success_at: text,
  last_attempt_at: text,
  last_finished_at: text,
  attempt_status: text,
  error_code: text,
  revision: text,
});
const source = z.object({
  id: z.string(),
  name: z.string(),
  system: z.enum(["warehouse", "feishu", "nmc"]),
  locator: z.string(),
  table_name: z.string(),
  source_title: z.string(),
  source_url: z.string().url(),
  pages: z.array(z.string()),
  kind: z.string(),
  datasets: z.array(dataset),
  platform_state: z.enum(["syncing", "failed", "pending", "available", "unavailable"]),
  upstream: z.object({
    state: z.enum(["unchecked", "checking", "failed", "changed", "unchanged"]),
    checked_at: text,
    observed_at: text,
    web_updated_at: text,
    web_checked_at: text,
    stale: z.boolean(),
    next_check_at: text.optional(),
    check_times: z.array(z.string()).optional(),
    observation: z.record(z.string(), z.unknown()),
    accepted_at: text,
    error_code: text,
    duration_ms: z.number().nullable(),
  }),
});
export const dataStatusSchema = z.object({
  data: z.object({
    sources: z.array(source),
    processing: z.array(
      dataset.extend({ state: z.string(), generated_at: text, pages: z.array(z.string()) }),
    ),
    monitor: z.object({
      enabled: z.boolean(),
      interval_seconds: z.number(),
      source_schedule_enabled: z.boolean().optional(),
      warehouse_times: z.array(z.string()).optional(),
      feishu_times: z.array(z.string()).optional(),
    }),
  }),
  meta: z.object({ generated_at: z.string() }),
});
export type DataStatus = z.infer<typeof dataStatusSchema>["data"];
export type SourceStatus = z.infer<typeof source>;
export type DatasetStatus = z.infer<typeof dataset>;
export const pageNames: Record<string, string> = {
  overview: "运营中心",
  products: "商品数据",
  promotion: "推广分析",
  sales: "店铺销售",
  aftersales: "售后分析",
};

/** 仅真实运行中的检查、接收或计算需要短时跟踪；排队/待重算不算运行。 */
export function hasRunningUpdate(data: DataStatus | undefined) {
  return Boolean(
    data?.sources.some(
      (source) =>
        source.upstream.state === "checking" ||
        source.platform_state === "syncing" ||
        source.datasets.some((item) => item.attempt_status === "running"),
    ) ||
    data?.processing.some((item) => item.state === "syncing" || item.attempt_status === "running"),
  );
}

/** 入口60秒查状态；弹窗打开立即读取，仅运行时15秒跟踪，空闲/读取失败停止轮询。 */
export function useDataUpdateStatus(open = false) {
  const query = useQuery({
    queryKey: ["data-update-status"],
    queryFn: async ({ signal }) =>
      dataStatusSchema.parse(await getJson("/api/v1/data-status", signal)),
    staleTime: 0,
    refetchInterval: (current) =>
      !open
        ? 60_000
        : current.state.status !== "error" && hasRunningUpdate(current.state.data?.data)
          ? 15_000
          : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const wasOpen = useRef(false);
  const refetch = query.refetch;
  useEffect(() => {
    if (open && !wasOpen.current) void refetch({ cancelRefetch: false });
    wasOpen.current = open;
  }, [open, refetch]);
  return query;
}
export function upstreamLabel(row: SourceStatus) {
  if (row.upstream.state === "failed") return { text: "检查失败", color: "error" };
  if (row.upstream.stale) return { text: "检查已过期", color: "warning" };
  return {
    unchecked: { text: "尚未检查", color: "default" },
    checking: { text: "正在检查", color: "processing" },
    failed: { text: "检查失败", color: "error" },
    changed: { text: "发现变化", color: "processing" },
    unchanged: { text: "未发现新变化", color: "success" },
  }[row.upstream.state];
}
export const platformLabels: Record<string, { text: string; color: string }> = {
  syncing: { text: "正在同步", color: "processing" },
  failed: { text: "最近同步失败", color: "error" },
  pending: { text: "等待同步", color: "warning" },
  available: { text: "数据可用", color: "success" },
  unavailable: { text: "尚无可用数据", color: "default" },
};
