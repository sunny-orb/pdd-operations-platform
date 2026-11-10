import { waitForPublicationBaseline } from "../../shared/api/publishedVersions";
import { useEffect, useRef, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { requestErrorText, shouldRetryRequest } from "../../shared/api/client";
import type { DomainId, DomainMeta, DomainPayload, DomainCard } from "./model";
export type DomainDiagnostics = Omit<DomainPayload, "card"> & {
  cardPatch?: Pick<Partial<DomainCard>, "pairs" | "note">;
};
export type DomainSpec<M = unknown> = {
  id: DomainId;
  meta: (signal: AbortSignal) => Promise<DomainMeta<M>>;
  load: (meta: DomainMeta<M>, signal: AbortSignal) => Promise<DomainPayload>;
  loadDiagnostics?: (meta: DomainMeta<M>, signal: AbortSignal) => Promise<DomainDiagnostics>;
};
export type DomainState = {
  id: DomainId;
  meta: DomainMeta | undefined;
  data: DomainPayload | undefined;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  observationLoading?: boolean;
  observationError?: string | null;
  retained?: boolean;
  statusError?: string | null;
  refresh: () => void;
  refreshStatus?: () => void;
};
const isConflict = (value: unknown) =>
  value !== null && typeof value === "object" && "status" in value && value.status === 409;
const queryPolicy = {
  retry: shouldRetryRequest,
  retryDelay: (attempt: number) => (attempt + 1) * 1000,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
};
/** 只合并同一版本/范围的诊断；补充来源说明及异常数字，不重算基础指标。 */
export function mergeDiagnostics(
  facts: DomainPayload,
  diagnostics: DomainDiagnostics,
): DomainPayload {
  const { cardPatch, ...result } = diagnostics;
  return {
    ...facts,
    ...result,
    card: {
      ...facts.card,
      pairs: facts.card.pairs.map(
        (field) => cardPatch?.pairs?.find((p) => p.label === field.label) ?? field,
      ),
      note: [facts.card.note, cardPatch?.note].filter(Boolean).join(" "),
    },
  };
}
/** 元信息、指标及诊断独立恢复；只消费本地发布结果，查询键绑定版本和经营范围。 */
export function useDomain<M>(spec: DomainSpec<M>): DomainState {
  const client = useQueryClient();
  const meta = useQuery({
    queryKey: ["overview", spec.id, "meta"],
    queryFn: async ({ signal }) => {
      await waitForPublicationBaseline(client, signal);
      return spec.meta(signal);
    },
    ...queryPolicy,
  });
  const version = meta.data?.version ?? "";
  const scope = JSON.stringify([meta.data?.date, meta.data?.scopeKey]);
  const enabled = Boolean(meta.data && version);
  const facts = useQuery({
    queryKey: ["overview", spec.id, "facts", version, scope],
    queryFn: ({ signal }) => spec.load(meta.data!, signal),
    enabled,
    ...queryPolicy,
  });
  const diagnostics = useQuery({
    queryKey: ["overview", spec.id, "diagnostics", version, scope],
    queryFn: ({ signal }) => spec.loadDiagnostics!(meta.data!, signal),
    enabled: enabled && Boolean(spec.loadDiagnostics),
    ...queryPolicy,
  });
  const retried = useRef(new Set<string>());
  const conflict = isConflict(facts.error) || isConflict(diagnostics.error);
  const refetchMeta = meta.refetch;
  useEffect(() => {
    const key = `${version}:${scope}`;
    if (conflict && version && !retried.current.has(key)) {
      retried.current.add(key);
      void refetchMeta();
    }
  }, [conflict, version, scope, refetchMeta]);
  const [retained, setRetained] = useState<{ data: DomainPayload; meta: DomainMeta }>();
  const current = useMemo(
    () =>
      facts.data
        ? diagnostics.data && spec.loadDiagnostics
          ? mergeDiagnostics(facts.data, diagnostics.data)
          : facts.data
        : undefined,
    [facts.data, diagnostics.data, spec.loadDiagnostics],
  );
  if (current && meta.data && (retained?.data !== current || retained.meta !== meta.data)) {
    setRetained({ data: current, meta: meta.data });
  }
  const showingRetained = !current && Boolean(retained);
  const data = current ?? retained?.data;
  const diagnosticError = spec.loadDiagnostics ? requestErrorText(diagnostics.error) : null;
  const observationLoading = Boolean(spec.loadDiagnostics && enabled && diagnostics.isPending);
  const error =
    requestErrorText(meta.error) ??
    requestErrorText(facts.error) ??
    (diagnosticError ? `风险与建议读取失败：${diagnosticError}` : null);
  const refresh = () => {
    void meta.refetch().then((next) => {
      if (
        !next.error &&
        next.data?.version === version &&
        JSON.stringify([next.data?.date, next.data?.scopeKey]) === scope
      ) {
        void facts.refetch();
        if (spec.loadDiagnostics) void diagnostics.refetch();
      }
    });
  };
  return {
    id: spec.id,
    // 保留值的日期与其原版本一致，不能把新版元信息日期贴到旧指标上。
    meta: showingRetained ? retained?.meta : meta.data,
    data,
    retained: showingRetained,
    loading: !data && (meta.isPending || (enabled && facts.isPending)),
    refreshing: meta.isFetching || facts.isFetching || diagnostics.isFetching,
    observationLoading,
    observationError: diagnosticError,
    error,
    statusError: requestErrorText(meta.error),
    refreshStatus: () => {
      void meta.refetch();
    },
    refresh,
  };
}
