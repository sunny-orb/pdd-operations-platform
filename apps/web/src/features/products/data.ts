import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import dayjs from "dayjs";
import { ApiError, get, params } from "./api";
import { meta, options } from "./contracts";
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
export function useOptions(scope: Scope, state: ProductsState, needle: string, name: ScopeName) {
  const keyword = useDebounced(needle);
  const base = { ...scope, products: [], keyword: keyword || scope.keyword, size: 100 };
  const invalid = scopeError(scope, state.metadata.data?.date_from, state.metadata.data?.date_to);
  const query = useInfiniteQuery({
    queryKey: ["m1", "options", name, state.version, base, scope.products],
    enabled: !!state.version && !invalid,
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) => {
      if (invalid) throw new ApiError(422, invalid);
      const p = params({ ...base, page: pageParam }, state.version);
      scope.products.forEach((id) => p.append("selected_listing_ids", id));
      p.set("scope", name);
      return get("options", options, p, signal);
    },
    getNextPageParam: (last, all) => (all.length * 100 < last.total ? all.length + 1 : undefined),
    retry: false,
  });
  useEffect(() => {
    if (query.error) state.stale(query.error);
  }, [query.error, state]);
  return query;
}
