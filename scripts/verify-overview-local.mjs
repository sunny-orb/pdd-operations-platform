/** 通过真实本地API核验中心适配，不启动浏览器、不读上游或经营快照。 */
import { createServer } from "vite";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nativeFetch = globalThis.fetch;
const timings = [];
const responses = new Map();
globalThis.fetch = async (input, init) => {
  const url = new URL(String(input), "http://127.0.0.1:8000");
  if (url.origin !== "http://127.0.0.1:8000" || !url.pathname.startsWith("/api/v1/"))
    throw new Error("只允许本地只读API");
  const start = performance.now();
  try {
    const response = await nativeFetch(url, init);
    if (response.ok) responses.set(url.pathname, await response.clone().json());
    return response;
  } finally {
    timings.push({ endpoint: url.pathname, ms: Math.round(performance.now() - start) });
  }
};
const server = await createServer({
  root: path.join(root, "apps/web"),
  server: { middlewareMode: true, watch: null, hmr: false, host: "127.0.0.1" },
  appType: "custom",
  logLevel: "error",
});
try {
  const adapters = await server.ssrLoadModule("/src/features/overview/adapters.ts");
  const names = ["salesSpec", "productsSpec", "promotionSpec", "aftersalesSpec"];
  const outcomes = await Promise.allSettled(
    names.map(async (name) => {
      const spec = adapters[name];
      const signal = new AbortController().signal;
      const meta = await spec.meta(signal);
      const result = await spec.load(meta, signal);
      if (!meta.version) throw new Error("缺真实版本");
      const raw = responses.get(`/api/v1/${meta.id}/overview`)?.data;
      const pair = (label) => result.card.pairs.find((p) => p.label === label)?.value;
      if (meta.id === "sales") {
        const target = responses.get("/api/v1/sales/targets").data;
        assert.equal(result.card.value, target.month_actual);
        assert.equal(pair("月度目标"), target.month_target);
        assert.equal(pair("目标达成"), target.month_ratio);
        assert.equal(
          pair("最新入仓日 GMV"),
          responses.get("/api/v1/sales/performance").data.shop_revenue,
        );
      } else if (meta.id === "products") {
        assert.equal(result.card.value, raw.metrics.revenue.value);
        assert.equal(pair("日环比"), raw.metrics.revenue.mom);
        assert.equal(pair("同比"), raw.metrics.revenue.yoy);
        assert.equal(result.observations.length, raw.risks.length);
      } else if (meta.id === "promotion") {
        const metric = (key) => raw.metrics[key]?.value;
        assert.equal(result.card.value, metric("roi"));
        assert.equal(pair("花费"), metric("spend"));
        assert.equal(pair("交易额"), metric("revenue"));
      } else {
        assert.equal(
          result.card.value,
          raw.metrics.find((m) => m.key === "total_rate")?.value ?? null,
        );
        assert.equal(
          pair("退款总额"),
          raw.risk_metrics.find((m) => m.key === "total_amount")?.value ?? null,
        );
        assert.equal(result.observations.length, raw.priorities.length);
        assert.equal(result.actions.length, raw.actions.length);
      }
      return {
        domain: meta.id,
        date: meta.date,
        version: meta.version,
        card: result.card,
        observations: result.observations.length,
        actions: result.actions.length,
      };
    }),
  );
  const rows = outcomes.map((r, i) =>
    r.status === "fulfilled" ? r.value : { domain: names[i], error: String(r.reason) },
  );
  const weather = await nativeFetch("http://127.0.0.1:8000/api/v1/weather/publication").then((r) =>
    r.json(),
  );
  if (!weather.data?.id || weather.data.document.sources.length !== 2)
    throw new Error("天气真实发布缺失");
  console.log(
    JSON.stringify(
      {
        domains: rows,
        weather: {
          id: weather.data.id,
          issue: weather.data.document.issue_time,
          sources: weather.data.document.sources.map((s) => ({
            name: s.name,
            issue: s.issue_time,
          })),
          temperature_status: weather.data.document.today_temperature.status,
        },
        http: timings,
      },
      null,
      2,
    ),
  );
  if (outcomes.some((r) => r.status === "rejected")) process.exitCode = 1;
} finally {
  await server.close();
  globalThis.fetch = nativeFetch;
}
