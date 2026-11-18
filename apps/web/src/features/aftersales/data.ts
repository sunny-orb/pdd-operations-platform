import { waitForPublicationBaseline } from "../../shared/api/publishedVersions";
import { shouldRetryRequest } from "../../shared/api/client";
import { usePublishedDomainRefresh } from "../../shared/api/usePublishedDomainRefresh";
import { useEffect, useRef } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useSearchRestoreKey } from "../../shared/components/useSearchRestoreKey";
import { z } from "zod";
import { get, params, AftersalesError } from "./api";
import { metadata, overview, options, analysis } from "./contracts";
import { readScope, patchScope, resetScope, type Scope } from "./state";
function useReportMetadata(reportId: string, enabled = true) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["m5", "meta", reportId],
    enabled,
    queryFn: async ({ signal }) => {
      await waitForPublicationBaseline(client, signal);
      return get(
        "meta",
        metadata,
        new URLSearchParams(reportId ? { report_id: reportId } : {}),
        signal,
      );
    },
    retry: (count, error: Error) => shouldRetryRequest(count, error),
    retryDelay: (attempt) => (attempt + 1) * 1000,
    placeholderData: keepPreviousData,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  });
}

/** 默认报告跟随当前目录；自选报告保留。发布变化只查本地，旧请求使用AbortSignal取消。 */
export function useAftersales(includeDetails = false) {
  const [search, setSearch] = useSearchParams();
  const searchRestoreKey = useSearchRestoreKey();
  const selectedReport = search.get("report_id") || "";
  // 页头读平台最新发布的数据；历史报告单独读取自身元信息。
  const latestMeta = useReportMetadata("");
  const selectedMeta = useReportMetadata(selectedReport, Boolean(selectedReport));
  const meta = selectedReport ? selectedMeta : latestMeta;
  const refreshMetadata = () => {
    void meta.refetch();
    if (selectedReport) void latestMeta.refetch();
  };
  usePublishedDomainRefresh("aftersales", refreshMetadata);
  const version = meta.data?.meta.data_version ?? null;
  const scope = readScope(search, meta.data?.data.default_report_id ?? "");
  const enabled = meta.isSuccess;
  const overviewResult = useResource("overview", overview, scope, version, enabled, "summary");
  const detailsResult = useResource(
    "overview",
    overview,
    scope,
    version,
    enabled && includeDetails && overviewResult.isSuccess,
    "full",
  );
  const analysisResult = useResource("analysis", analysis, scope, version, enabled);
  const optionsResult = useResource("options", options, scope, version, enabled);
  const retried = useRef(new Set<string>());
  const conflict = [
    overviewResult.error,
    analysisResult.error,
    optionsResult.error,
    detailsResult.error,
  ].find((error) => error instanceof AftersalesError && error.status === 409);
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
    searchRestoreKey,
    change,
    reset,
    version,
    meta,
    latestMeta,
    refreshMetadata,
    overview: overviewResult,
    details: detailsResult,
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
  detail?: "full" | "summary",
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
    queryKey: ["m5", path, version, requestScope, detail],
    enabled,
    queryFn: ({ signal }) => {
      const query = params(requestScope, version);
      if (detail) query.set("detail", detail);
      return get(path, schema, query, signal);
    },
    retry: (count, error: Error) => shouldRetryRequest(count, error),
    retryDelay: (attempt) => (attempt + 1) * 1000,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}
export type AftersalesState = ReturnType<typeof useAftersales>;
