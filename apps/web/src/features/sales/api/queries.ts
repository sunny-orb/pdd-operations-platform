import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../../shared/api/client";
import type { FiltersResponse, SalesResult, Scope } from "./contracts";
import { getSalesFilters, getSalesResult } from "./salesApi";

const initialScope: Scope = { month: "", from: "", to: "", shop: "" };
/** 取消旧组请求；仅展示当前筛选同版本结果，连续409只自动恢复一次。 */
export function useSalesData() {
  const [filters, setFilters] = useState<FiltersResponse | null>(null);
  const [scope, setScope] = useState(initialScope);
  const [result, setResult] = useState<SalesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const recovered = useRef(false);
  const key = JSON.stringify([filters?.meta.data_version, scope.from, scope.to, scope.shop]);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    async function load() {
      try {
        if (!filters) {
          const next = await getSalesFilters(signal);
          if (signal.aborted) return;
          setFilters(next);
          setScope((previous) =>
            previous.month
              ? previous
              : {
                  month: next.data.default_month,
                  from: next.data.recommended_date_from,
                  to: next.data.recommended_date_to,
                  shop: "",
                },
          );
          return;
        }
        if (!scope.from || !scope.to) return;
        const next = await getSalesResult(scope, filters.meta.data_version, signal);
        if (signal.aborted) return;
        setResult({ ...next, key });
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
  }, [filters, scope, key, refresh]);
  const changeScope = useCallback((next: Partial<Scope>) => {
    recovered.current = false;
    setError(null);
    setScope((previous) => ({ ...previous, ...next }));
  }, []);
  const retry = useCallback(() => {
    recovered.current = false;
    setError(null);
    setResult(null);
    setFilters(null);
    setRefresh((n) => n + 1);
  }, []);
  const invalidateVersion = useCallback(() => {
    if (!recovered.current) {
      recovered.current = true;
      setFilters(null);
      setResult(null);
    } else setError("数据版本持续变化，请重新读取本地数据。");
  }, []);
  return {
    invalidateVersion,
    filters,
    scope,
    result: result?.key === key ? result : null,
    error,
    changeScope,
    retry,
  };
}
