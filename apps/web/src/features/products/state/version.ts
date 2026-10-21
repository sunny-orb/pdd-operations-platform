type Update = { action: string; endpoint: string; epoch: number; status?: number };
const expected = new Set(["brief", "overview", "scores", "trend", "diagnosis", "alerts", "days"]);
/** 每代最多一次连续恢复；仅接受真实请求事件，旧代错误和缓存通知不能触发切版。 */
export function createVersionCoordinator() {
  let epoch = 0;
  let recovering = false;
  const successes = new Set<string>();
  return {
    get epoch() {
      return epoch;
    },
    reset() {
      epoch += 1;
      recovering = false;
      successes.clear();
    },
    handle(event: Update): "ignore" | "reload" | "lock" | "recovered" {
      if (event.epoch !== epoch) return "ignore";
      if (event.action === "success" && recovering) {
        if (expected.has(event.endpoint)) successes.add(event.endpoint);
        if (successes.size === expected.size) {
          recovering = false;
          successes.clear();
          return "recovered";
        }
      }
      if (event.action !== "error" || event.status !== 409) return "ignore";
      if (recovering) return "lock";
      recovering = true;
      epoch += 1;
      successes.clear();
      return "reload";
    },
  };
}
