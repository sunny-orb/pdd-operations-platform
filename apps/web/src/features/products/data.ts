import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import dayjs from "dayjs";
import { ApiError, get, params } from "./api";
import { catalog, meta } from "./contracts";
import { catalogOptions } from "./catalog";
import { patchScope, readScope, scopeError, type Scope, type ScopeName } from "./scope";

const DEFAULT_PERIOD_DAYS = 31;

export function useProducts() {
  const [search, setSearch] = useSearchParams();
  const metadata = useQuery({
    queryKey: ["m1", "meta"],
    queryFn: ({ signal }) => get("meta", meta, new URLSearchParams(), signal),
    retry: false,
    refetchInterval: 45000,
    refetchOnWindowFocus: "always",
  });
  const retried = useRef(new Set<string>());
  const version = metadata.data?.data_version ?? "";
  const stale = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409 && !retried.current.has(version)) {
      retried.current.add(version);
      void metadata.refetch();
    }
  };
  const from = metadata.data
    ? [
        dayjs(metadata.data.date_to)
          .subtract(DEFAULT_PERIOD_DAYS - 1, "day")
          .format("YYYY-MM-DD"),
        metadata.data.date_from,
      ]
        .sort()
        .at(-1)!
    : "";
  const scope = (name: ScopeName) => readScope(search, name, from, metadata.data?.date_to ?? "");
  const change = (name: ScopeName, patch: Partial<Scope>) =>
    setSearch((p) => patchScope(p, name, patch, from, metadata.data?.date_to ?? ""), {
      replace: true,
    });
  const reset = (name: ScopeName) =>
    setSearch(
      (p) => {
        const next = new URLSearchParams(p);
        [...next.keys()].filter((k) => k.startsWith(`${name}_`)).forEach((k) => next.delete(k));
        return next;
      },
      { replace: true },
    );
  return { metadata, version, scope, change, reset, stale };
}
export type ProductsState = ReturnType<typeof useProducts>;
export function useResource<T>(
  path: string,
  schema: z.ZodType<T>,
  scope: Scope,
  state: ProductsState,
) {
  const invalid = scopeError(scope, state.metadata.data?.date_from, state.metadata.data?.date_to);
  const query = useQuery({
    queryKey: ["m1", path, state.version, scope],
    enabled: !!state.version && !invalid,
    queryFn: ({ signal }) => {
      if (invalid) throw new ApiError(422, invalid);
      return get(path, schema, params(scope, state.version), signal);
    },
    retry: false,
    // 只在同一版本、同一筛选的评分翻页期间保留旧页，避免列表塌陷造成滚动跳动。
    // 换筛选、日期、版本或接口时立即撤下旧结果，不让旧商品冒充新范围的数据。
    placeholderData: (previousData, previousQuery) => {
      const key = previousQuery?.queryKey;
      if (path !== "scores" || key?.[1] !== path || key[2] !== state.version) return undefined;
      const previousScope = key[3] as Scope;
      const sameScope = Object.entries(scope).every(([field, value]) => {
        if (field === "page") return true;
        const previous = previousScope[field as keyof Scope];
        return Array.isArray(value) && Array.isArray(previous)
          ? value.length === previous.length &&
              value.every((item, index) => item === previous[index])
          : value === previous;
      });
      return sameScope ? previousData : undefined;
    },
  });
  useEffect(() => {
    if (query.error) state.stale(query.error);
  }, [query.error, state]);
  return invalid && state.version
    ? { ...query, data: undefined, isPending: false, error: new ApiError(422, invalid) }
    : query;
}
export function useDebounced(value: string) {
  const [current, setCurrent] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setCurrent(value), 250);
    return () => clearTimeout(t);
  }, [value]);
  return current;
}
/** 每个版本/日期仅取一次轻量目录；维度与搜索即时计算，不读取经营快照。 */
export function useOptions(scope: Scope, state: ProductsState, needle: string, name: ScopeName) {
  const directory = name === "score" ? "score" : "main";
  const invalid = scopeError(scope, state.metadata.data?.date_from, state.metadata.data?.date_to);
  const query = useQuery({
    queryKey: ["m1", "catalog", directory, state.version, scope.from, scope.to],
    enabled: !!state.version && !invalid,
    queryFn: ({ signal }) => {
      if (invalid) throw new ApiError(422, invalid);
      return get(
        "catalog",
        catalog,
        new URLSearchParams({
          date_from: scope.from,
          date_to: scope.to,
          data_version: state.version,
          scope: directory,
        }),
        signal,
      );
    },
    // 发布版本不变时目录不会变；新版本进入独立缓存键。
    staleTime: Infinity,
    retry: false,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === directory && previousQuery.queryKey[3] === state.version
        ? previous
        : undefined,
  });
  useEffect(() => {
    if (query.error) state.stale(query.error);
  }, [query.error, state]);
  const data = useMemo(
    () => (query.data ? catalogOptions(query.data, scope, needle) : undefined),
    [query.data, scope, needle],
  );
  return {
    ...query,
    catalog: invalid ? undefined : query.data,
    data: invalid ? undefined : data,
    error: invalid && state.version ? new ApiError(422, invalid) : query.error,
  };
}
