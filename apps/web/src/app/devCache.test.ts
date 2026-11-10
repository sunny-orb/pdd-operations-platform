// @vitest-environment node
import { fileURLToPath } from "node:url";
import { resolveConfig } from "vite";
import { describe, expect, it } from "vitest";

const root = fileURLToPath(new URL("../../", import.meta.url));

describe("开发页面与只读核验实例的缓存边界", () => {
  it("中间件核验不会覆盖正在运行的页面依赖缓存", async () => {
    const browser = await resolveConfig({ root }, "serve");
    const verification = await resolveConfig(
      { root, server: { middlewareMode: true }, appType: "custom" },
      "serve",
    );
    expect(verification.cacheDir).not.toBe(browser.cacheDir);
  });

  it("核验调用方已指定的独立缓存目录保持有效", async () => {
    const cacheDir = fileURLToPath(new URL("../../node_modules/.vite-explicit", import.meta.url));
    const verification = await resolveConfig(
      { root, cacheDir, server: { middlewareMode: true }, appType: "custom" },
      "serve",
    );
    expect(verification.cacheDir).toBe(cacheDir);
  });
});
