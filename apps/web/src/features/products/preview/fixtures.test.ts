import { describe, expect, it } from "vitest";
import { scores } from "./fixtures";

describe("评分展示样例自洽性", () => {
  it("完整分和已验证分等于指标得分之和，待验证权重等于缺项权重", () => {
    for (const score of scores) {
      const points = score.metrics.reduce((total, metric) => total + (metric.points ?? 0), 0);
      const pending = score.metrics
        .filter((metric) => metric.points === null)
        .reduce((total, metric) => total + metric.weight, 0);
      expect(score.verifiedScore).toBe(points);
      expect(score.pendingWeight).toBe(pending);
      expect(score.score).toBe(score.status === "complete" ? points : null);
    }
  });
  it("缺项不展示已达档位，已计分样例对应静态四档", () => {
    for (const score of scores)
      for (const metric of score.metrics) {
        if (metric.points === null) {
          expect(metric.grade).toBe("待验证");
          expect(metric.value).toBeNull();
        } else {
          const tier = metric.tiers.filter((threshold) => Number(metric.value) >= threshold).length;
          expect(metric.points).toBe((metric.weight * tier) / 4);
          expect(metric.grade).toBe(["未达基础", "基础", "良好", "优秀", "突出"][tier]);
        }
      }
  });
});
