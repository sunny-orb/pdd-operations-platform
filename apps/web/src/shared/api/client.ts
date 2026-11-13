/** API错误只暴露稳定编码和面向使用者的提示，不透传服务内部详情。 */
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const unavailable = "接口暂时不可用，请稍后重试。";
const messages: Record<string, string> = {
  DATABASE_BUSY: "服务暂时繁忙，请稍后重试。",
  QUERY_TIMEOUT: "数据读取超时，请稍后重试。",
  DATABASE_UNAVAILABLE: unavailable,
  REQUEST_TIMEOUT: "数据读取超时，请稍后重试。",
};

function isCancelled(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/** 查询库传入已失败次数；只为瞬时故障提供最多两次自动恢复。 */
export function shouldRetryRequest(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2 || isCancelled(error)) return false;
  return error instanceof Error && "status" in error && typeof error.status === "number"
    ? [408, 429, 502, 503, 504].includes(error.status)
    : error instanceof TypeError;
}

/** 供非查询库调用方使用同一恢复预算；等待期间离页也立即取消。 */
export async function withRequestRetry<T>(
  request: () => Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  for (let failures = 0; ; failures++) {
    signal.throwIfAborted();
    try {
      return await request();
    } catch (error) {
      signal.throwIfAborted();
      if (!shouldRetryRequest(failures, error)) throw error;
      await new Promise<void>((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          reject(signal.reason);
        };
        const timer = setTimeout(
          () => {
            signal.removeEventListener("abort", abort);
            resolve();
          },
          (failures + 1) * 1000,
        );
        signal.addEventListener("abort", abort, { once: true });
      });
    }
  }
}

/** 主动取消不展示错误；接口和网络故障使用中文提示。 */
export function requestErrorText(error: unknown): string | null {
  if (!error || isCancelled(error)) return null;
  if (error instanceof ApiError) return error.message || unavailable;
  if (error instanceof TypeError) return "网络连接暂时不可用，请稍后重试。";
  return "数据读取失败，请稍后重试。";
}

/** 请求同源只读 API；区分调用方取消与十秒超时，返回由调用方校验的响应。 */
export async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(
    () => controller.abort(new ApiError(messages.REQUEST_TIMEOUT, 408, "REQUEST_TIMEOUT")),
    10_000,
  );
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      let code: string | undefined;
      try {
        const body: unknown = await response.json();
        if (body && typeof body === "object" && "error" in body) {
          const error = body.error;
          if (
            error &&
            typeof error === "object" &&
            "code" in error &&
            typeof error.code === "string"
          )
            code = error.code;
        }
      } catch {
        // 网关可能返回HTML；状态码仍保留供有界恢复使用。
      }
      const message =
        response.status === 409
          ? "数据版本已更新，请重新读取。"
          : (code && messages[code]) || unavailable;
      throw new ApiError(message, response.status, code);
    }
    return await response.json();
  } catch (error) {
    // 响应体读取也可能被中断，以本次控制器的首个取消原因区分超时和离页。
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
