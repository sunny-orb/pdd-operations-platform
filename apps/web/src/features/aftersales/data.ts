import { waitForPublicationBaseline } from "../../shared/api/publishedVersions";
import { shouldRetryRequest } from "../../shared/api/client";
import { usePublishedDomainRefresh } from "../../shared/api/usePublishedDomainRefresh";
import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { get, params, AftersalesError } from "./api";
import { metadata, overview, options, analysis } from "./contracts";
import { readScope, patchScope, resetScope, type Scope } from "./state";
/** 默认报告跟随当前目录；自选报告保留。发布变化只查本地，旧请求使用AbortSignal取消。 */
export function useAftersales() {
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const selectedReport = search.get("report_id") || "";
  const meta = useQuery({
    queryKey: ["m5", "meta", selectedReport],
    queryFn: async ({ signal }) => {
      await waitForPublicationBaseline(client, signal);
      return get(
        "meta",
        metadata,
        new URLSearchParams(selectedReport ? { report_id: selectedReport } : {}),
        signal,
      );
    },
    retry: (count, error: Error) => shouldRetryRequest(count, error),
    retryDelay: (attempt) => (attempt + 1) * 1000,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  });
  usePublishedDomainRefresh("aftersales", () => {
    void meta.refetch();
  });
  const version = meta.data?.meta.data_version ?? null;
  const scope = readScope(search, meta.data?.data.default_report_id ?? "");
  const enabled = meta.isSuccess;
  const overviewResult = useResource("overview", overview, scope, version, enabled);
  const analysisResult = useResource("analysis", analysis, scope, version, enabled);
  const optionsResult = useResource("options", options, scope, version, enabled);
  const retried = useRef(new Set<string>());
  const conflict = [overviewResult.error, analysisResult.error, optionsResult.error].find(
    (error) => error instanceof AftersalesError && error.status === 409,
  );
  useEffect(() => {
    const key = version ?? "unavailable";
    if (conflict && !retried.current.has(key)) {
      retried.current.add(key);
      void meta.refetch();
    }
  }, [conflict, version, meta]);
  const change = (patch: Partial<Scope>) =>
    setSearch((current) => patchScope(current, patch), { replace: true });
  const reset = () => setSearch((current) => resetScope(current), { replace: true });
  return {
    scope,
    change,
    reset,
    version,
    meta,
    overview: overviewResult,
    analysis: analysisResult,
    options: optionsResult,
  };
}
function useResource<T>(
  path: string,
  schema: z.ZodType<T>,
  scope: Scope,
  version: string | null,
  enabled: boolean,
) {
  // 分析标签/分页不影响总览，目录只依赖报告；按实际API输入隔离查询键及请求条件。
  const summaryScope: Scope = { ...scope, view: "category", page: 1, size: 25 };
  const requestScope =
    path === "analysis"
      ? scope
      : path === "options"
        ? { ...summaryScope, category: "", keyword: "", focus: "all" as const }
        : summaryScope;
  return useQuery({
    queryKey: ["m5", path, version, requestScope],
    enabled,
    queryFn: ({ signal }) => get(path, schema, params(requestScope, version), signal),
    retry: (count, error: Error) => shouldRetryRequest(count, error),
    retryDelay: (attempt) => (attempt + 1) * 1000,
    refetchOnWindowFocus: false,
  });
}
export type AftersalesState = ReturnType<typeof useAftersales>;
