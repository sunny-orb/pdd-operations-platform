import { createContext, useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Envelope, Filters, RequestOptions, Scope } from "./contracts";
import { requestProducts } from "./productsApi";

export const ProductBootstrapContext = createContext<{
  ready: boolean;
  error: Error | null;
  notConnected: boolean;
  retry: () => void;
} | null>(null);

export const ProductDataVersionContext = createContext<string | undefined>(undefined);

/** 隔离范围、版本与分页，取消过期请求；不保留其他筛选的旧结果。 */
export function useProductQuery<T>(
  endpoint: string,
  scope: Scope,
  filters: Filters,
  options: RequestOptions,
  enabled = true,
) {
  const contextVersion = useContext(ProductDataVersionContext);
  const bootstrap = useContext(ProductBootstrapContext);
  const effectiveOptions = { ...options, dataVersion: options.dataVersion ?? contextVersion };
  return useQuery<Envelope<T>, Error>({
    queryKey: ["products", endpoint, scope, filters, effectiveOptions],
    queryFn: ({ signal }) => requestProducts<T>(endpoint, scope, filters, effectiveOptions, signal),
    enabled:
      enabled &&
      (options.mode !== "api" ||
        endpoint === "filters" ||
        ((bootstrap?.ready ?? true) &&
          !!effectiveOptions.dataVersion &&
          effectiveOptions.dataVersion !== "not-connected")),
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data?.computationState === "pending" && query.state.dataUpdateCount < 12
        ? 5_000
        : false,
    refetchIntervalInBackground: false,
  });
}
