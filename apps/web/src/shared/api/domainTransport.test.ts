import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { get as products, ApiError } from "../../features/products/api";
import { get as promotion, PromotionError } from "../../features/promotion/api";
import { get as aftersales, AftersalesError } from "../../features/aftersales/api";
import { shouldRetryRequest } from "./client";

const domains = [
  { name: "商品", get: products, ErrorType: ApiError },
  { name: "推广", get: promotion, ErrorType: PromotionError },
  { name: "售后", get: aftersales, ErrorType: AftersalesError },
];
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
for (const domain of domains) {
  const request = (signal?: AbortSignal) =>
    domain.get("meta", z.unknown(), new URLSearchParams(), signal);
  describe(`${domain.name}请求恢复边界`, () => {
    it.each([502, 503, 504])("网关HTML错误保留状态%d并进入有界重试", async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response("<html>gateway</html>", { status })),
      );
      const error = await request().catch((value: unknown) => value);
      expect(error).toBeInstanceOf(domain.ErrorType);
      expect(error).toMatchObject({ status });
      expect(shouldRetryRequest(0, error)).toBe(true);
      expect(shouldRetryRequest(2, error)).toBe(false);
    });
    it("十秒未响应返回领域超时，不无限悬挂", async () => {
      vi.useFakeTimers();
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_url, options: RequestInit) =>
            new Promise((_resolve, reject) => {
              options.signal?.addEventListener("abort", () => reject(options.signal!.reason), {
                once: true,
              });
            }),
        ),
      );
      const result = request().catch((value: unknown) => value);
      await vi.advanceTimersByTimeAsync(10_000);
      let settled = false;
      void result.then(() => {
        settled = true;
      });
      await Promise.resolve();
      expect(settled).toBe(true);
      expect(await result).toBeInstanceOf(domain.ErrorType);
      expect(await result).toMatchObject({ status: 408 });
    });
    it("409仍交由原领域版本恢复，不进入通用重试", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ error: { code: "VERSION_CHANGED" } }), { status: 409 }),
          ),
      );
      const error = await request().catch((value: unknown) => value);
      expect(error).toBeInstanceOf(domain.ErrorType);
      expect(error).toMatchObject({ status: 409 });
      expect(shouldRetryRequest(0, error)).toBe(false);
    });
  });
}
