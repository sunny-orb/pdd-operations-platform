import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";
import { get, params, PromotionError } from "./api";
import { metadata, overview, days, options } from "./contracts";
import { dateError, readScope, writeScope, type Mode, type Scope } from "./state";
import { filterOptions, type FilterOptions, type ProductOption } from "./filter-options";

const shanghaiDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const selectedProductOptions = options.pick({ products: true });

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
    // 同版元信息检查也通知，诊断中依赖“今日”的状态才能随上海业务日更新。
    notifyOnChangeProps: "all",
  });
  const data = meta.data?.data;
  const version = meta.data?.meta.data_version ?? "";
  const scope = useMemo(
    () => readScope(search, mode, data?.recommended_from ?? "", data?.recommended_to ?? ""),
    [search, mode, data?.recommended_from, data?.recommended_to],
  );
  const invalid = dateError(scope);
  // 范围键立即切换以取消旧请求；仅商品经营查询等待连续选择停止，翻页不等待。
  const scopeKey = JSON.stringify({ ...scope, page: 1, size: 25 });
  const [settledScope, setSettledScope] = useState({ mode, version, key: scopeKey });
  const newContext = settledScope.mode !== mode || settledScope.version !== version;
  if (newContext) setSettledScope({ mode, version, key: scopeKey });
  const isScopePending = mode === "product" && !newContext && settledScope.key !== scopeKey;
  useEffect(() => {
    if (!isScopePending) return;
    const timer = setTimeout(() => setSettledScope({ mode, version, key: scopeKey }), 200);
    return () => clearTimeout(timer);
  }, [isScopePending, scopeKey, mode, version]);
  const [needle, setNeedle] = useState("");
  const retried = useRef(new Set<string>());
  const stale = (error: unknown) => {
    const key = `${mode}:${version}`;
    if (error instanceof PromotionError && error.status === 409 && !retried.current.has(key)) {
      retried.current.add(key);
      void meta.refetch();
    }
  };
  const optionsResult = useResource(
    "options",
    options,
    mode,
    mode === "product"
      ? {
          ...scope,
          shop: "",
          category: "",
          level: "",
          operator: "",
          products: [],
          keyword: "",
          page: 1,
        }
      : { ...scope, page: 1 },
    version,
    invalid,
    [],
    mode === "product",
  );
  // 首屏/新日期优先返回完整目录；目录失败仍让指标独立查询，不冒充无经营数据。
  const businessReady = (mode !== "product" || !optionsResult.isPending) && !isScopePending;
  const overviewResult = useResource(
    "overview",
    overview,
    mode,
    scope,
    version,
    invalid,
    [],
    false,
    businessReady,
    mode === "product" ? { detail: "display" } : {},
  );
  const daysResult = useResource(
    "days",
    days,
    mode,
    scope,
    version,
    invalid,
    [],
    false,
    businessReady,
  );
  // 已见名称仅用于已选项回显；日期/版本目录更新不能隐式清空用户选择。
  const [known, setKnown] = useState<{
    source: unknown;
    selectedSource: unknown;
    rows: ProductOption[];
    shops: FilterOptions["shops"];
  }>({
    source: null,
    selectedSource: null,
    rows: [],
    shops: [],
  });
  const catalog = optionsResult.data?.data;
  const missingNames = useMemo(() => {
    const available = new Set(
      [...known.rows, ...(catalog?.selected_products ?? []), ...(catalog?.products ?? [])].map(
        (row) => row.value,
      ),
    );
    return [...new Set(scope.products)].filter((id) => !available.has(id)).sort();
  }, [known.rows, catalog, scope.products]);
  // 首次URL可能指向日期目录以外的商品，只补未知身份名称，不扩大目录或经营范围。
  const selectedOptions = useQuery({
    queryKey: ["m2", "product", "options/selected", version, missingNames],
    enabled: mode === "product" && !!version && !optionsResult.isPending && missingNames.length > 0,
    queryFn: ({ signal }) => {
      const query = new URLSearchParams({ mode: "product", data_version: version });
      missingNames.forEach((id) => query.append("selected_ids", id));
      return get("options/selected", selectedProductOptions, query, signal);
    },
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const selection = selectedOptions.data?.data;
  if (
    mode === "product" &&
    ((catalog && known.source !== catalog) || (selection && known.selectedSource !== selection))
  ) {
    const identities = new Map(known.rows.map((row) => [row.value, row]));
    [
      ...(selection?.products ?? []),
      ...(catalog?.selected_products ?? []),
      ...(catalog?.products ?? []),
    ].forEach((row) => identities.set(row.value, row));
    const shops = new Map(
      [...known.shops, ...(catalog?.shops ?? [])].map((shop) => [shop.value, shop]),
    );
    setKnown({
      source: catalog ?? known.source,
      selectedSource: selection ?? known.selectedSource,
      rows: [...identities.values()],
      shops: [...shops.values()],
    });
  }
  const localOptions = useMemo(
    () => catalog && filterOptions(catalog, scope, needle, known.rows),
    [catalog, scope, needle, known.rows],
  );
  const selectedProducts = useMemo(
    () => known.rows.filter((row) => scope.products.includes(row.value)),
    [known.rows, scope.products],
  );
  const displayOptions =
    mode === "product" && optionsResult.data
      ? {
          ...optionsResult,
          data: {
            ...optionsResult.data,
            data: localOptions!,
          },
        }
      : optionsResult;
  useEffect(() => {
    [overviewResult.error, daysResult.error, optionsResult.error, selectedOptions.error].forEach(
      (error) => {
        if (error) stale(error);
      },
    );
  });
  const change = (patch: Partial<Scope>) => {
    if (mode === "product" && Object.keys(patch).some((key) => !["page", "size"].includes(key)))
      setNeedle("");
    setSearch((current) => writeScope(current, mode, { page: 1, ...patch }), { replace: true });
  };
  const setMode = (next: Mode) => {
    setNeedle("");
    setSearch(
      (current) => {
        const result = new URLSearchParams(current);
        result.set("mode", next);
        return result;
      },
      { replace: true },
    );
  };
  return {
    mode,
    setMode,
    scope,
    queryScope: scope,
    isScopePending,
    businessBusy:
      !invalid &&
      (isScopePending ||
        (mode === "product" && optionsResult.isPending) ||
        overviewResult.isPending ||
        overviewResult.isFetching ||
        daysResult.isPending ||
        daysResult.isFetching),
    change,
    meta,
    version,
    invalid,
    overview: overviewResult,
    days: daysResult,
    options: displayOptions,
    selectedProducts,
    selectedOptions,
    shopOptions: mode === "product" ? known.shops : (catalog?.shops ?? []),
    needle,
    setNeedle,
    stale,
    businessReady,
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
  catalog = false,
  enabled = true,
  extraParams: Record<string, string> = {},
) {
  // 逐日明细及按需诊断清单消费分页；目录、总览和诊断摘要不受明细页码影响。
  const paged = path === "days" || path === "diagnosis/objects";
  const requestScope = paged ? scope : { ...scope, page: 1, size: 25 };
  const keepStorePage = path === "days" && mode === "store";
  const diagnosisDay = path.startsWith("diagnosis") ? shanghaiDay.format(new Date()) : null;
  const pageScope = JSON.stringify([mode, path, version, { ...requestScope, page: 1 }, selected]);
  const query = useQuery({
    queryKey: [
      "m2",
      mode,
      path,
      version,
      requestScope,
      selected,
      catalog,
      extraParams,
      diagnosisDay,
    ],
    enabled: enabled && !!version && !invalid,
    queryFn: async ({ signal }) => {
      if (invalid) throw new PromotionError(422, invalid);
      const p = params(mode, requestScope, version);
      selected.forEach((id) => p.append("selected_ids", id));
      if (catalog) p.set("catalog", "true");
      Object.entries(extraParams).forEach(([key, value]) => p.set(key, value));
      const result = await get(path, schema, p, signal);
      if (catalog) {
        const directory = options.parse(result.data);
        if (
          directory.products.length !== directory.total ||
          new Set(directory.products.map((row) => row.value)).size !== directory.total
        ) {
          throw new PromotionError(502, "筛选选项未完整加载，请重试");
        }
      }
      return result;
    },
    retry: false,
    refetchOnWindowFocus: false,
    // 固定版本同范围复用；诊断的“今日”状态另按上海业务日隔离。
    staleTime: Infinity,
  });
  const [lastPage, setLastPage] = useState<{ scope: string; data: typeof query.data } | null>(null);
  if (
    keepStorePage &&
    query.data &&
    (lastPage?.scope !== pageScope || lastPage.data !== query.data)
  ) {
    setLastPage({ scope: pageScope, data: query.data });
  }
  // 仅同筛选、同版本翻页时保留上一成功页；失败也保留，并由明细区提供重试。
  const heldData = keepStorePage && lastPage?.scope === pageScope ? lastPage.data : undefined;
  return invalid && version
    ? { ...query, data: undefined, error: new PromotionError(422, invalid), isPending: false }
    : { ...query, data: query.data ?? heldData };
}
