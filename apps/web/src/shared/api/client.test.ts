import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJson, requestErrorText, shouldRetryRequest } from "./client";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function pendingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_path, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          const signal = options.signal!;
          if (signal.aborted) reject(signal.reason);
          else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    ),
  );
}

describe("公共请求失败边界", () => {
  it("十秒请求超时转为可恢复中文错误", async () => {
    vi.useFakeTimers();
    pendingFetch();
    const result = getJson("/api/v1/test").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await result).toMatchObject({ status: 408, code: "REQUEST_TIMEOUT" });
    expect(requestErrorText(await result)).toContain("超时");
  });
  it("调用方取消保留原始原因，不伪装超时", async () => {
    pendingFetch();
    const controller = new AbortController();
    const result = getJson("/api/v1/test", controller.signal).catch((error: unknown) => error);
    const reason = new DOMException("navigation", "AbortError");
    controller.abort(reason);
    expect(await result).toBe(reason);
    expect(requestErrorText(await result)).toBeNull();
    expect(shouldRetryRequest(0, await result)).toBe(false);
  });
  it("识别服务繁忙编码，不向页面泄露内部错误文字", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { code: "DATABASE_BUSY", message: "private SQL details" } }),
            { status: 503 },
          ),
        ),
    );
    const error = await getJson("/api/v1/test").catch((value: unknown) => value);
    expect(error).toMatchObject({ status: 503, code: "DATABASE_BUSY" });
    expect(requestErrorText(error)).toContain("繁忙");
    expect(requestErrorText(error)).not.toContain("private");
  });
  it("仅瞬时错误最多重试两次", () => {
    for (const status of [408, 429, 502, 503, 504]) {
      expect(shouldRetryRequest(0, new ApiError("", status))).toBe(true);
      expect(shouldRetryRequest(1, new ApiError("", status))).toBe(true);
      expect(shouldRetryRequest(2, new ApiError("", status))).toBe(false);
    }
    expect(shouldRetryRequest(0, new TypeError("Failed to fetch"))).toBe(true);
    for (const error of [
      new ApiError("", 409),
      new ApiError("", 403),
      new Error("格式不符合约定"),
      new DOMException("", "AbortError"),
    ]) {
      expect(shouldRetryRequest(0, error)).toBe(false);
    }
    expect(requestErrorText(new Error("internal SQL"))).not.toContain("SQL");
  });
});

it("领域HTTP错误共用有限恢复；非查询库调用离页取消等待", async () => {
  vi.useFakeTimers();
  const { withRequestRetry } = await import("./client");
  const error = Object.assign(new Error("DATABASE_BUSY"), { status: 503 });
  expect(shouldRetryRequest(0, error)).toBe(true);
  const request = vi.fn().mockRejectedValueOnce(error).mockResolvedValue("recovered");
  const controller = new AbortController();
  const result = withRequestRetry(request, controller.signal);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await result).toBe("recovered");
  expect(request).toHaveBeenCalledTimes(2);
  const failing = vi.fn().mockRejectedValue(error);
  const cancelled = withRequestRetry(failing, controller.signal).catch((value: unknown) => value);
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  expect(await cancelled).toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(3000);
  expect(failing).toHaveBeenCalledTimes(1);
});

it("HTTP409是版本变化，不误报服务不可用且不按旧版本重试", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "DATA_VERSION_CHANGED" } }), { status: 409 }),
      ),
  );
  const error = await getJson("/api/v1/test").catch((value: unknown) => value);
  expect(requestErrorText(error)).toContain("版本");
  expect(requestErrorText(error)).not.toContain("不可用");
  expect(shouldRetryRequest(0, error)).toBe(false);
});
