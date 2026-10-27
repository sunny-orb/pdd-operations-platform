import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { get, params, PromotionError } from "./api";
import { metadata, overview, days, options } from "./contracts";
import { dateError, readScope, writeScope, type Mode, type Scope } from "./state";

/** 45秒及窗口聚焦仅重读本地元信息；发布变化后整组查询固定新版本。 */
export function usePromotion() {
  const [search, setSearch] = useSearchParams();
  const mode: Mode = search.get("mode") === "product" ? "product" : "store";
  const meta = useQuery({
    queryKey: ["m2", "meta", mode],
    queryFn: ({ signal }) => get("meta", metadata, new URLSearchParams({ mode }), signal),
    retry: false,
    refetchInterval: 45000,
    refetchOnWindowFocus: "always",
  });
  const data = meta.data?.data;
  const version = meta.data?.meta.data_version ?? "";
  const scope = readScope(search, mode, data?.recommended_from ?? "", data?.recommended_to ?? "");
  const invalid = dateError(scope);
  const [needle, setNeedle] = useState("");
  const [keyword, setKeyword] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setKeyword(needle), 250);
    return () => clearTimeout(id);
  }, [needle]);
  const retried = useRef(new Set<string>());
  const stale = (error: unknown) => {
    const key = `${mode}:${version}`;
    if (error instanceof PromotionError && error.status === 409 && !retried.current.has(key)) {
      retried.current.add(key);
      void meta.refetch();
    }
  };
  const overviewResult = useResource("overview", overview, mode, scope, version, invalid);
  const daysResult = useResource("days", days, mode, scope, version, invalid);
  const optionsResult = useResource(
    "options",
    options,
    mode,
    { ...scope, keyword: keyword || scope.keyword, page: 1 },
    version,
    invalid,
    scope.products,
  );
  useEffect(() => {
    [overviewResult.error, daysResult.error, optionsResult.error].forEach((error) => {
      if (error) stale(error);
    });
  });
  const change = (patch: Partial<Scope>) =>
    setSearch((current) => writeScope(current, mode, { page: 1, ...patch }), { replace: true });
  const setMode = (next: Mode) =>
    setSearch(
      (current) => {
        const result = new URLSearchParams(current);
        result.set("mode", next);
        return result;
      },
      { replace: true },
    );
  return {
    mode,
    setMode,
    scope,
    change,
    meta,
    version,
    invalid,
    overview: overviewResult,
    days: daysResult,
    options: optionsResult,
    needle,
    setNeedle,
    stale,
  };
}
export type PromotionState = ReturnType<typeof usePromotion>;
export function useResource<T>(
  path: string,
  schema: z.ZodType<T>,
  mode: Mode,
  scope: Scope,
  version: string,
  invalid: string | null,
  selected: string[] = [],
) {
  const query = useQuery({
    queryKey: ["m2", mode, path, version, scope, selected],
    enabled: !!version && !invalid,
    queryFn: ({ signal }) => {
      if (invalid) throw new PromotionError(422, invalid);
      const p = params(mode, scope, version);
      selected.forEach((id) => p.append("selected_ids", id));
      return get(path, schema, p, signal);
    },
    retry: false,
    refetchOnWindowFocus: false,
  });
  return invalid && version
    ? { ...query, data: undefined, error: new PromotionError(422, invalid), isPending: false }
    : query;
}
