import { useEffect, useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { getJson, shouldRetryRequest } from "./client";
const publishedVersions = z.object({
  data: z.object({
    products: z.string(),
    promotion: z.string(),
    sales: z.string(),
    aftersales: z.string(),
  }),
});
export type PublishedVersions = z.infer<typeof publishedVersions>["data"];
const baselineStates = new WeakMap<QueryClient, { failed: boolean; recovery: number }>();
function baselineState(client: QueryClient) {
  let state = baselineStates.get(client);
  if (!state) {
    state = { failed: false, recovery: 0 };
    baselineStates.set(client, state);
  }
  return state;
}
const versionQueryOptions = (client: QueryClient) => ({
  queryKey: ["overview", "published-versions"] as const,
  queryFn: async ({ signal }: { signal: AbortSignal }) => {
    const data = publishedVersions.parse(await getJson("/api/v1/published-versions", signal)).data;
    const state = baselineState(client);
    if (state.failed) {
      state.recovery++;
      state.failed = false;
    }
    return data;
  },
  retry: shouldRetryRequest,
  retryDelay: (attempt: number) => (attempt + 1) * 1000,
});
/** 首次基线先于业务meta；复用共享在途请求，版本检查失败不阻断经营读取。 */
export async function waitForPublicationBaseline(client: QueryClient, signal: AbortSignal) {
  signal.throwIfAborted();
  // 已失败的首查由共享轮询恢复，业务重试不再次放大轻量查询。
  if (baselineState(client).failed) return;
  try {
    await client.ensureQueryData(versionQueryOptions(client));
  } catch {
    signal.throwIfAborted();
    baselineState(client).failed = true;
    // 轻量检查不可用时继续本地业务查询；不以失败结果建立发布基线。
  }
  signal.throwIfAborted();
}
/** 可见页面每五分钟只读发布指纹；停止后台轮询，所有消费者共用同一查询。 */
export function usePublishedVersions() {
  const client = useQueryClient();
  const [initialRecovery] = useState(() => baselineState(client).recovery);
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const versions = useQuery({
    ...versionQueryOptions(client),
    enabled: visible,
    refetchInterval: visible ? 300_000 : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  return { ...versions, recoveredBaseline: baselineState(client).recovery > initialRecovery };
}
