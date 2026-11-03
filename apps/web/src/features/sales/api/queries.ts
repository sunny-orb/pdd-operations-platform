import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../../../shared/api/client";
import type { FiltersResponse, SalesResult, Scope } from "./contracts";
import { getSalesFilters, getSalesResult } from "./salesApi";

const initialScope: Scope = { month: "", from: "", to: "", shop: "" };
const scopeKeys = ["month", "from", "to", "shop"] as const;

function isDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** URL只接收来源元信息允许的月份、日历范围及店铺身份；无效范围不回退取数。 */
function readScope(search: URLSearchParams, filters: FiltersResponse) {
  const invalid = (reason: string) => ({ scope: initialScope, error: `链接范围无效：${reason}` });
  if (scopeKeys.some((key) => search.getAll(key).length > 1)) return invalid("筛选参数重复。");
  const rawFrom = search.get("from"),
    rawTo = search.get("to");
  if ((rawFrom === null) !== (rawTo === null)) return invalid("开始与结束日期须同时提供。");
  if (rawFrom !== null && (!isDate(rawFrom) || !isDate(rawTo!)))
    return invalid("日期须为有效的ISO日历日期。");
  const month = search.get("month") ?? rawFrom?.slice(0, 7) ?? filters.data.default_month;
  const option = filters.data.months.find((item) => item.month === month);
  if (!/^\d{4}-\d{2}$/.test(month) || !option) return invalid("月份不在已知经营月份中。");
  const explicitMonth = search.has("month");
  const from = rawFrom ?? (explicitMonth ? option.date_from : filters.data.recommended_date_from);
  const to = rawTo ?? (explicitMonth ? option.date_to : filters.data.recommended_date_to);
  const earliest = filters.data.months.reduce(
    (lo, item) => (item.date_from < lo ? item.date_from : lo),
    option.date_from,
  );
  if (
    from > to ||
    from < earliest ||
    to > filters.data.calendar_through ||
    !filters.data.months.some((item) => item.month === from.slice(0, 7)) ||
    !filters.data.months.some((item) => item.month === to.slice(0, 7))
  )
    return invalid("日期倒置或超出可查询经营日历。");
  const rawShop = search.get("shop");
  const shop =
    rawShop === null
      ? null
      : filters.data.shops.find(
          (item) => item.shop_id === rawShop || item.source_shop_id === rawShop,
        );
  if (rawShop !== null && !shop) return invalid("店铺不在当前已知店铺集合中。");
  return { scope: { month, from, to, shop: shop?.shop_id ?? "" }, error: null };
}

const resultCache = new Map<string, SalesResult>();
const MAX_CACHE = 8;
export function _resetCacheForTesting() {
  resultCache.clear();
}

/** 取消旧组请求；仅展示当前筛选同版本结果，连续409只自动恢复一次。 */
export function useSalesData() {
  const location = useLocation();
  const navigate = useNavigate();
  const [filters, setFilters] = useState<FiltersResponse | null>(null);
  const [latestStatus, setLatestStatus] = useState<FiltersResponse | null>(null);
  const search = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const parsed = useMemo(
    () => (filters ? readScope(search, filters) : { scope: initialScope, error: null }),
    [filters, search],
  );
  const scope = parsed.scope;
  const scopeError = parsed.error;
  const [result, setResult] = useState<SalesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const recovered = useRef(false);
  const key = JSON.stringify([filters?.meta.data_version, scope.from, scope.to, scope.shop]);
  useEffect(() => {
    recovered.current = false;
  }, [location.search]);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    async function load() {
      try {
        if (!filters) {
          const next = await getSalesFilters(signal);
          if (signal.aborted) return;
          setFilters(next);
          setLatestStatus(next);
          return;
        }
        if (scopeError || !scope.from || !scope.to) return;
        const cached = resultCache.get(key);
        if (cached) {
          setResult({ ...cached, key });
          setError(null);
          return;
        }
        const next = await getSalesResult(scope, filters.meta.data_version, signal);
        if (signal.aborted) return;
        const entry = { ...next, key };
        if (resultCache.size >= MAX_CACHE) resultCache.delete(resultCache.keys().next().value!);
        resultCache.set(key, entry);
        setResult(entry);
        setError(null);
      } catch (cause) {
        if (signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 409 && !recovered.current) {
          recovered.current = true;
          setFilters(null);
          return;
        }
        setError(
          cause instanceof ApiError && cause.status === 503
            ? "店铺日数据尚未接入或本地服务暂不可用。"
            : "读取失败或数据契约不符，请重试；未使用样例替代。",
        );
      }
    }
    void load();
    return () => controller.abort();
  }, [filters, scope, scopeError, key, refresh]);
  const changeScope = useCallback(
    (next: Partial<Scope>) => {
      recovered.current = false;
      setError(null);
      const query = new URLSearchParams(location.search);
      const updated = { ...scope, ...next };
      if (next.from !== undefined || next.to !== undefined || next.month !== undefined) {
        for (const field of scopeKeys) {
          if (updated[field]) query.set(field, updated[field]);
          else query.delete(field);
        }
      } else if (next.shop !== undefined) {
        if (next.shop) query.set("shop", next.shop);
        else query.delete("shop");
      }
      const nextSearch = query.toString();
      if (nextSearch !== location.search.slice(1))
        navigate({
          pathname: location.pathname,
          search: nextSearch ? `?${nextSearch}` : "",
          hash: location.hash,
        });
    },
    [location, navigate, scope],
  );
  const resetScope = useCallback(() => {
    const query = new URLSearchParams(location.search);
    scopeKeys.forEach((field) => query.delete(field));
    setError(null);
    navigate({
      pathname: location.pathname,
      search: query.size ? `?${query}` : "",
      hash: location.hash,
    });
  }, [location, navigate]);
  useEffect(() => {
    if (!filters) return;
    const controller = new AbortController();
    const checkVersion = async () => {
      try {
        const next = await getSalesFilters(controller.signal);
        if (controller.signal.aborted) return;
        // 失败保旧或无变化成功不换版，来源尝试/成功时间仍须刷新。
        setLatestStatus(next);
        setError((previous) =>
          previous === "自动检查最新版本失败，保留上次成功数据，请重试。" ? null : previous,
        );
        if (next.meta.data_version === filters.meta.data_version) return;
        recovered.current = false;
        setFilters(next);
      } catch {
        if (!controller.signal.aborted)
          setError("自动检查最新版本失败，保留上次成功数据，请重试。");
      }
    };
    const timer = window.setInterval(() => void checkVersion(), 45000);
    const focus = () => void checkVersion();
    window.addEventListener("focus", focus);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
    };
  }, [filters]);
  const retry = useCallback(() => {
    recovered.current = false;
    setError(null);
    setResult(null);
    setFilters(null);
    resultCache.clear();
    setRefresh((n) => n + 1);
  }, []);
  const invalidateVersion = useCallback(() => {
    if (!recovered.current) {
      recovered.current = true;
      resultCache.clear();
      setFilters(null);
      setResult(null);
    } else setError("数据版本持续变化，请重新读取本地数据。");
  }, []);
  return {
    invalidateVersion,
    filters:
      filters && latestStatus?.meta.data_version === filters.meta.data_version
        ? {
            ...filters,
            meta: latestStatus.meta,
            data: { ...filters.data, sources: latestStatus.data.sources },
          }
        : filters,
    scope,
    result:
      !scopeError && result?.key === key
        ? {
            ...result,
            meta:
              latestStatus?.meta.data_version === result.meta.data_version
                ? latestStatus.meta
                : result.meta,
          }
        : null,
    error: scopeError ?? error,
    scopeError,
    changeScope,
    resetScope,
    retry,
  };
}
