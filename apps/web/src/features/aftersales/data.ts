import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { get, params, AftersalesError } from "./api";
import { metadata, overview, options, analysis } from "./contracts";
import { readScope, patchScope, resetScope, type Scope } from "./state";
/** 默认报告跟随当前目录；自选报告保留。轮询/聚焦只查本地，旧请求使用AbortSignal取消。 */
export function useAftersales() {
  const [search, setSearch] = useSearchParams();
  const selectedReport = search.get("report_id") || "";
  const meta = useQuery({
    queryKey: ["m5", "meta", selectedReport],
    queryFn: ({ signal }) =>
      get(
        "meta",
        metadata,
        new URLSearchParams(selectedReport ? { report_id: selectedReport } : {}),
        signal,
      ),
    retry: false,
    refetchInterval: 45000,
    refetchOnWindowFocus: "always",
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
  return useQuery({
    queryKey: ["m5", path, version, scope],
    enabled,
    queryFn: ({ signal }) => get(path, schema, params(scope, version), signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
}
export type AftersalesState = ReturnType<typeof useAftersales>;
