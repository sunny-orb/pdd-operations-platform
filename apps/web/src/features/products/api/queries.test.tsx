// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";
import { ProductBootstrapContext, ProductDataVersionContext, useProductQuery } from "./queries";
import { requestProducts } from "./productsApi";
import { buildDefaultFilters } from "../state/filters";
vi.mock("./productsApi", () => ({
  requestProducts: vi.fn().mockResolvedValue({
    data: null,
    availability: "not_connected",
    computationState: "ready",
    meta: {
      dataVersion: "v1",
      dataThrough: null,
      lastSuccessAt: null,
      completeness: "unknown",
      missingReasons: [],
      isPreview: false,
    },
  }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function setup(mode: "api" | "preview", ready: boolean, version?: string, endpoint = "overview") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>
        <ProductBootstrapContext.Provider
          value={{ ready, error: null, notConnected: false, retry: () => undefined }}
        >
          <ProductDataVersionContext.Provider value={version}>
            {children}
          </ProductDataVersionContext.Provider>
        </ProductBootstrapContext.Provider>
      </QueryClientProvider>
    );
  }
  return renderHook(
    () =>
      useProductQuery(endpoint, "overview", buildDefaultFilters(), { mode, scenario: "normal" }),
    { wrapper: Wrapper },
  );
}
describe("真实API元信息就绪门控", () => {
  it("bootstrap未ready不能请求经营数据，即使有版本", async () => {
    const { result } = setup("api", false, "v1");
    expect(result.current.fetchStatus).toBe("idle");
    expect(requestProducts).not.toHaveBeenCalled();
  });
  it.each([undefined, "not-connected"])("未获得有效版本 %s 不能请求经营数据", async (version) => {
    const { result } = setup("api", true, version);
    expect(result.current.fetchStatus).toBe("idle");
    expect(requestProducts).not.toHaveBeenCalled();
  });
  it("ready且context有版本才请求，并自动传入该版本", async () => {
    setup("api", true, "v1");
    await waitFor(() => expect(requestProducts).toHaveBeenCalledOnce());
    expect(vi.mocked(requestProducts).mock.calls[0][3].dataVersion).toBe("v1");
  });
  it("filters引导请求不被自身bootstrap条件阻断", async () => {
    setup("api", false, undefined, "filters");
    await waitFor(() => expect(requestProducts).toHaveBeenCalledOnce());
  });
  it("开发样例不受真实API门控影响", async () => {
    setup("preview", false);
    await waitFor(() => expect(requestProducts).toHaveBeenCalledOnce());
  });
});
