import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import type { DomainId, DomainMeta, DomainPayload } from "./model";
export type DomainSpec<M = unknown> = {
  id: DomainId;
  meta: (signal: AbortSignal) => Promise<DomainMeta<M>>;
  load: (meta: DomainMeta<M>, signal: AbortSignal) => Promise<DomainPayload>;
};
export type DomainState = {
  id: DomainId;
  meta: DomainMeta | undefined;
  data: DomainPayload | undefined;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  statusError?: string | null;
  refresh: () => void;
  refreshStatus?: () => void;
};
const errorText = (value: unknown) =>
  value instanceof Error ? value.message : "查询失败，保留上次已发布结果。";
const isConflict = (value: unknown) =>
  value !== null && typeof value === "object" && "status" in value && value.status === 409;
/** 每域独立元信息与固定版本结果；轮询只查本地，同版失败状态也可及时显示。 */
export function useDomain<M>(spec: DomainSpec<M>): DomainState {
  const meta = useQuery({
    queryKey: ["overview", spec.id, "meta"],
    queryFn: ({ signal }) => spec.meta(signal),
    retry: false,
    refetchInterval: 45000,
    refetchOnWindowFocus: "always",
  });
  const version = meta.data?.version ?? "";
  const scope = JSON.stringify([meta.data?.date, meta.data?.scopeKey]);
  const facts = useQuery({
    queryKey: ["overview", spec.id, "facts", version, scope],
    queryFn: ({ signal }) => spec.load(meta.data!, signal),
    enabled: meta.isSuccess && Boolean(version),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const retried = useRef(new Set<string>());
  useEffect(() => {
    const key = `${version}:${scope}`;
    if (isConflict(facts.error) && version && !retried.current.has(key)) {
      retried.current.add(key);
      void meta.refetch();
    }
  }, [facts.error, version, scope, meta]);
  const refresh = () => {
    void meta.refetch().then((next) => {
      if (
        next.data?.version === version &&
        JSON.stringify([next.data?.date, next.data?.scopeKey]) === scope
      )
        void facts.refetch();
    });
  };
  return {
    id: spec.id,
    meta: meta.data,
    data: facts.data,
    loading: meta.isPending || (meta.isSuccess && Boolean(version) && facts.isPending),
    refreshing: meta.isFetching || facts.isFetching,
    error: meta.error ? errorText(meta.error) : facts.error ? errorText(facts.error) : null,
    statusError: meta.error ? errorText(meta.error) : null,
    refreshStatus: () => {
      void meta.refetch();
    },
    refresh,
  };
}
