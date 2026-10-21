import { afterEach, describe, expect, it, vi } from "vitest";
import { getSystemStatus } from "./system";

afterEach(() => vi.unstubAllGlobals());

describe("系统状态契约", () => {
  it("正确展示未配置数据库，而非把 API 连通当成数据接入", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            data: { database_configured: false, business_data: "not_integrated" },
            meta: { queried_at: "2026-10-21T00:00:00+00:00" },
          }),
        ),
      ),
    );
    expect((await getSystemStatus()).databaseConfigured).toBe(false);
  });

  it("拒绝缺字段响应，避免将异常结构当正常状态", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    await expect(getSystemStatus()).rejects.toThrow("格式不符合约定");
  });

  it("接口错误明确失败，不产生默认经营数字", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })));
    await expect(getSystemStatus()).rejects.toThrow("接口暂时不可用");
  });
});
