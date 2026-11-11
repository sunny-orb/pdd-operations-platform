import { mergeDiagnostics } from "./data";
import { afterEach, expect, it, vi } from "vitest";
import { productsSpec } from "./adapters";
afterEach(() => vi.unstubAllGlobals());
function setup() {
  let date = "2026-11-01",
    version = "v1",
    status = "partial",
    total = 8,
    start = "2026-09-01";
  const fetcher = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => ({
      meta: { data_version: version },
      data: path.includes("products/meta")
        ? {
            data_version: version,
            date_from: start,
            date_to: date,
            observed_rows: "100",
            product_count: "8",
            success_at: null,
            complete_through: null,
            completeness: "unknown",
            profile_basis: "source",
            latest_attempt: {},
            sources: [],
          }
        : path.includes("products/risks")
          ? {
              period: {
                start: new URL(path, "http://local").searchParams.get("date_from"),
                end: date,
              },
              evaluation_date: date,
              latest_data_date: date,
              baseline_period: { start: "2026-10-25", end: "2026-10-31" },
              trend_period: { start: "2026-10-26", end: date },
              generation_method: "rules",
              rule_version: "product-risk-traffic-v2",
              impact_kind: "estimated",
              status,
              total,
              counts: {
                selected: 10,
                excluded: 0,
                evaluated: status === "insufficient_data" ? 0 : 8,
                unevaluated: status === "insufficient_data" ? 10 : 2,
                flagged: total,
                unflagged: 8 - total,
              },
              risks: Array.from({ length: total }, (_, i) => ({
                listing_id: `listing-${i}`,
                shop_id: "shop",
                product_id: `p${i}`,
                shop_name: "门店",
                product_name: `商品${i}`,
                category: null,
                level: null,
                operator: "资料运营人",
                period_revenue: "100",
                alert: {
                  label: "来源异常",
                  priority: "P0",
                  score: 99,
                  current: "25",
                  baseline: "100",
                  change: "-0.75",
                  type: "number",
                  metric: "visitors",
                  baseline_method: "daily_average",
                  trend: Array(7).fill("25"),
                  expected_loss: "0",
                  reasons: ["来源线索"],
                  action: "来源动作",
                  deadline: null,
                  status: "未启用",
                  basis: "原规则",
                },
              })),
            }
          : {
              metrics: {
                revenue: {
                  label: "交易额",
                  type: "currency",
                  value: "55",
                  previous: null,
                  previous_period: null,
                  yoy: null,
                  mom: null,
                  status: "observed",
                  reason: null,
                },
              },
              risks: [{ bad: "不能取这里的旧风险" }],
            },
    }),
  }));
  vi.stubGlobal("fetch", fetcher);
  return {
    fetcher,
    update: (p: {
      date?: string;
      version?: string;
      status?: string;
      total?: number;
      start?: string;
    }) => {
      date = p.date ?? date;
      version = p.version ?? version;
      status = p.status ?? status;
      total = p.total ?? total;
      start = p.start ?? start;
    },
  };
}
it("中心从独立风险接口取完整31日名单，日GMV独立；风险跳转不额外缩店铺池", async () => {
  const { fetcher } = setup();
  const signal = new AbortController().signal;
  const meta = await productsSpec.meta(signal);
  const d = mergeDiagnostics(
    await productsSpec.load(meta, signal),
    await productsSpec.loadDiagnostics!(meta, signal),
  );
  const urls = fetcher.mock.calls.map(([p]) => new URL(p, "http://local"));
  const risk = urls.find((u) => u.pathname.endsWith("/risks"))!;
  expect(risk.searchParams.get("date_from")).toBe("2026-10-02");
  expect(risk.searchParams.get("date_to")).toBe("2026-11-01");
  expect(risk.searchParams.get("data_version")).toBe("v1");
  const daily = urls.find((u) => u.pathname.endsWith("/overview"))!;
  expect(daily.searchParams.get("date_from")).toBe("2026-11-01");
  expect(d.observations).toHaveLength(8);
  expect(d.card.value).toBe("55");
  expect(d.observations[0].detail).toContain("-75.00%");
  expect(d.observations[0].scope).toContain("2026-10-25 至 2026-10-31");
  expect(d.observations[0].sourcePriority).toBe("P0");
  const link = new URL(d.observations[0].link, "http://local");
  expect(link.searchParams.get("main_from")).toBe("2026-10-02");
  expect(link.searchParams.get("main_listing")).toBe("listing-0");
  expect(link.searchParams.has("main_shop")).toBe(false);
  expect(link.hash).toBe("#risk");
  expect(d.actions[0]).toMatchObject({
    suggestion: "来源动作",
    assignee: "待分配",
    progress: "待核查",
  });
  expect(d.card.note).toContain("未评估 2");
});
it("最新版本与日期推进重新取数，跨月/接入不足31日夹到真实起日；未评空不作零", async () => {
  const { fetcher, update } = setup();
  const signal = new AbortController().signal;
  const first = await productsSpec.meta(signal);
  mergeDiagnostics(
    await productsSpec.load(first, signal),
    await productsSpec.loadDiagnostics!(first, signal),
  );
  update({
    version: "v2",
    date: "2027-01-05",
    start: "2026-12-20",
    status: "insufficient_data",
    total: 0,
  });
  const next = await productsSpec.meta(signal);
  const d = mergeDiagnostics(
    await productsSpec.load(next, signal),
    await productsSpec.loadDiagnostics!(next, signal),
  );
  const url = new URL(
    fetcher.mock.calls.filter(([u]) => u.includes("/risks"))[1][0],
    "http://local",
  );
  expect(url.searchParams.get("date_from")).toBe("2026-12-20");
  expect(url.searchParams.get("date_to")).toBe("2027-01-05");
  expect(url.searchParams.get("data_version")).toBe("v2");
  expect(d.observationState).toBe("pending_validation");
  expect(d.card.pairs.find((p) => p.label === "观察异常对象")?.value).toBeNull();
});

it("商品基础指标请求不等待风险接口，未读取风险不能当零异常", async () => {
  const { fetcher } = setup();
  const signal = new AbortController().signal;
  const meta = await productsSpec.meta(signal);
  const data = await productsSpec.load(meta, signal);
  expect(data.card.value).toBe("55");
  expect(data.observationState).toBe("not_available");
  expect(data.card.pairs.find((p) => p.label === "观察异常对象")?.value).toBeNull();
  expect(fetcher.mock.calls.some(([path]) => path.includes("/risks"))).toBe(false);
});
