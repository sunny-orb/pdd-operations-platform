import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import dayjs from "dayjs";
import { ApiError, get, params } from "./api";
import { meta, options } from "./contracts";
import { readScope, writeScope, type Scope, type ScopeName } from "./scope";

export function useProducts() {
  const [search, setSearch] = useSearchParams();
  const metadata = useQuery({
    queryKey: ["m1", "meta"],
    queryFn: ({ signal }) => get("meta", meta, new URLSearchParams(), signal),
    retry: false,
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
        dayjs(metadata.data.date_to).subtract(30, "day").format("YYYY-MM-DD"),
        metadata.data.date_from,
      ]
        .sort()
        .at(-1)!
    : "";
  const scope = (name: ScopeName) => readScope(search, name, from, metadata.data?.date_to ?? "");
  const change = (name: ScopeName, patch: Partial<Scope>) =>
    setSearch(
      (p) =>
        writeScope(p, name, {
          ...readScope(p, name, from, metadata.data?.date_to ?? ""),
          ...patch,
        }),
      { replace: true },
    );
  const reset = (name: ScopeName) =>
    setSearch(
      (p) =>
        writeScope(
          p,
          name,
          readScope(new URLSearchParams(), name, from, metadata.data?.date_to ?? ""),
        ),
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
  const query = useQuery({
    queryKey: ["m1", path, state.version, scope],
    enabled: !!state.version && !!scope.from,
    queryFn: ({ signal }) => get(path, schema, params(scope, state.version), signal),
    retry: false,
  });
  useEffect(() => {
    if (query.error) state.stale(query.error);
  }, [query.error, state]);
  return query;
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
  const query = useInfiniteQuery({
    queryKey: ["m1", "options", name, state.version, base, scope.products],
    enabled: !!state.version && !!scope.from,
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) => {
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
