import { describe, expect, it } from "vitest";
import { createVersionCoordinator } from "./version";

describe("版本冲突恢复", () => {
  it("只处理真实error动作，旧代请求不触发第二次切版", () => {
    const c = createVersionCoordinator();
    expect(c.handle({ action: "invalidate", endpoint: "overview", epoch: 0, status: 409 })).toBe(
      "ignore",
    );
    expect(c.handle({ action: "error", endpoint: "overview", epoch: 0, status: 409 })).toBe(
      "reload",
    );
    expect(c.epoch).toBe(1);
    expect(c.handle({ action: "error", endpoint: "alerts", epoch: 0, status: 409 })).toBe("ignore");
    expect(c.handle({ action: "error", endpoint: "overview", epoch: 1, status: 409 })).toBe("lock");
  });
  it("成功取得整组新版本后，允许后来独立的来源更新恢复", () => {
    const c = createVersionCoordinator();
    c.handle({ action: "error", endpoint: "overview", epoch: 0, status: 409 });
    for (const endpoint of ["brief", "overview", "scores", "trend", "diagnosis", "alerts"])
      expect(c.handle({ action: "success", endpoint, epoch: 1 })).toBe("ignore");
    expect(c.handle({ action: "success", endpoint: "days", epoch: 1 })).toBe("recovered");
    expect(c.handle({ action: "error", endpoint: "overview", epoch: 1, status: 409 })).toBe(
      "reload",
    );
    expect(c.epoch).toBe(2);
  });
});
