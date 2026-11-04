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
export type SourceStatus = z.infer<typeof source>;
export type DatasetStatus = z.infer<typeof dataset>;
export const pageNames: Record<string, string> = {
  overview: "运营中心",
  products: "商品数据",
  promotion: "推广分析",
  sales: "店铺销售",
  aftersales: "售后分析",
};

/** 弹窗卸载即取消；后台标签暂停轮询。缓存仅保留状态摘要，不触发任何来源请求。 */
export function useDataUpdateStatus() {
  return useQuery({
    queryKey: ["data-update-status"],
    queryFn: async ({ signal }) =>
      dataStatusSchema.parse(await getJson("/api/v1/data-status", signal)),
    staleTime: 0,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    retry: false,
  });
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
