import { afterEach, expect, it, vi } from "vitest";
import { mergeDiagnostics } from "./data";
import { aftersalesSpec, promotionSpec, salesSpec } from "./adapters";
import { aftersalesObjectKey, centerCounts, observationCoverage } from "./model";
import { syntheticResponse, testFilters, testMeta } from "../sales/testFixtures";
afterEach(() => vi.unstubAllGlobals());
const centerTargets = {
  meta: testMeta,
  data: {
    month_actual: "100",
    annual_actual: "1000",
    month_target: "200",
    annual_target: null,
    month_ratio: "0.5",
    annual_ratio: null,
    gap: "100",
    state: "observed",
  },
};

it("运营中心用同版本筛选响应的单日摘要展示零成交与覆盖店数", async () => {
  const fetchSales = vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes("/filters")
        ? {
            ...testFilters,
            data: {
              ...testFilters.data,
              latest_day: { date: "2026-10-26", shop_revenue: "0.00", observed_shop_count: 1 },
            },
          }
        : url.includes("/targets?")
          ? centerTargets
          : syntheticResponse(url),
  }));
  vi.stubGlobal("fetch", fetchSales);
  const signal = new AbortController().signal;
  const meta = await salesSpec.meta(signal);
  const { card } = await salesSpec.load(meta, signal);
  expect(card.value).toBe("100");
  expect(card.pairs).toContainEqual({ label: "最新入仓日 GMV", value: "0.00", type: "currency" });
  expect(card.sourceNote).toContain("最新日含 1/2 店铺");
  expect(fetchSales.mock.calls.map(([url]) => url.split("?")[0])).toEqual([
    "/api/v1/sales/filters",
    "/api/v1/sales/targets",
  ]);
});

it("运营中心目标尚未返回时已经请求同版本的单日销售和店铺汇总", async () => {
  let finishTargets!: () => void;
  const pendingTargets = new Promise<void>((resolve) => {
    finishTargets = resolve;
  });
  const fetchSales = vi.fn(async (url: string) => {
    if (url.includes("/targets?")) {
      await pendingTargets;
      return { ok: true, json: async () => centerTargets };
    }
    return { ok: true, json: async () => syntheticResponse(url) };
  });
  vi.stubGlobal("fetch", fetchSales);
  const signal = new AbortController().signal;
  const meta = await salesSpec.meta(signal);
  const card = salesSpec.load(meta, signal);
  try {
    const requests = fetchSales.mock.calls.map(([url]) => url);
    expect(requests).toContain("/api/v1/sales/targets?month=2026-10&data_version=synthetic-v1");
    for (const path of ["performance", "shops"]) {
      expect(requests).toContain(
        `/api/v1/sales/${path}?data_version=synthetic-v1&date_from=2026-10-26&date_to=2026-10-26`,
      );
    }
  } finally {
    finishTargets();
    await card;
  }
  const { card: result } = await card;
  expect(result.value).toBe("100");
  expect(result.pairs).toContainEqual({
    label: "最新入仓日 GMV",
    value: "30.50",
    type: "currency",
  });
  expect(result.sourceNote).toContain("最新日含 2/2 店铺");
});

it("GMV目标同版本失败与恢复改变中心查询状态，避免保留旧成功说明", async () => {
  let status = "failed";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ...testFilters,
        data: {
          ...testFilters.data,
          sources: {
            sales_targets: {
              state: "observed",
              observed_from: null,
              observed_through: null,
              source_updated_text: null,
              last_success_at: null,
              last_attempt_status: status,
              last_attempt_at: "2026-10-28T00:00:00Z",
            },
          },
        },
      }),
    })),
  );
  const signal = new AbortController().signal;
  const failed = await salesSpec.meta(signal);
  status = "succeeded";
  const recovered = await salesSpec.meta(signal);
  expect(recovered.version).toBe(failed.version);
  expect(recovered.scopeKey).not.toBe(failed.scopeKey);
  expect(failed.notes.join(" ")).toContain("failed");
  expect(recovered.notes.join(" ")).toContain("succeeded");
  expect(failed.updates?.find((s) => s.id === "sales_targets")?.attemptStatus).toBe("failed");
  expect(recovered.updates?.find((s) => s.id === "sales_targets")?.kind).toBe("target");
});
it("售后同版来源失败和新输入待重算如实进入中心状态", async () => {
  let status = "failed_retained",
    error: string | null = "SOURCE_READ_FAILED";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        data: {
          data_version: "same-version",
          status: "partial",
          default_report_id: "report",
          observed_to: "2026-10-27",
          complete_through: null,
          last_success_at: null,
          last_attempt_at: null,
          sources: [
            {
              code: "jst_returns",
              label: "售后",
              locator: null,
              status,
              observed_from: null,
              observed_to: "2026-10-27",
              last_success_at: null,
              last_attempt_at: null,
              error_code: error,
              note: "原输入保留",
            },
          ],
        },
        meta: { data_version: "same-version" },
      }),
    })),
  );
  const signal = new AbortController().signal;
  const failed = await aftersalesSpec.meta(signal);
  expect(failed.attemptStatus).toBe("failed");
  expect(failed.notes.join(" ")).toContain("SOURCE_READ_FAILED");
  status = "new_input_pending_report";
  error = null;
  const pending = await aftersalesSpec.meta(signal);
  expect(pending.version).toBe(failed.version);
  expect(pending.attemptStatus).toBe("pending_report");
  expect(failed.updates?.[0].attemptStatus).toBe("failed");
  expect(pending.updates?.[0].sourceStatus).toBe("new_input_pending_report");
});
it("售后只用明确报告和对象层级映射，不由同名标题补异常或分类", async () => {
  const source = {
    data_version: "v",
    status: "partial" as const,
    default_report_id: "report",
    observed_to: "2026-10-27",
    complete_through: null,
    last_success_at: null,
    last_attempt_at: null,
    sources: [],
  };
  const priority = {
    id: "overall",
    kind: "risk",
    title: "同名观察",
    detail: "原证据",
    verification: "复核",
  };
  let decisionStatus = "partial";
  let priorities = [
    { ...priority, object_level: "overview", object_key: "all", report_id: "report" },
    {
      ...priority,
      id: "category",
      object_level: "category",
      object_key: "帽子",
      report_id: "report",
    },
    { ...priority, id: "unknown" },
    {
      ...priority,
      id: "wrong-report",
      object_level: "overview",
      object_key: "all",
      report_id: "other",
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        data: {
          report: {
            id: "report",
            report_type: "custom",
            label: "报告",
            period_start: "2026-09-28",
            period_end: "2026-10-27",
            generated_at: null,
            document_url: null,
            sheet_url: null,
          },
          metrics: [],
          risk_metrics: [],
          decision: {
            title: "观察",
            status: decisionStatus,
            rate: null,
            amount: null,
            amount_share: null,
            change_pp: null,
            note: "待核",
          },
          priorities,
          actions: [],
          stages: [],
          size_breakdown: [],
          reasons: [],
          trend: [],
          scope_note: "报告期",
          quality_note: "来源待核",
          core_insights: [],
          category_risks: [],
          style_risks: [],
          category_actions: [],
          style_actions: [],
        },
        meta: { data_version: "v" },
      }),
    })),
  );
  const payload = await aftersalesSpec.load(
    {
      id: "aftersales",
      version: "v",
      date: "2026-10-27",
      completeThrough: null,
      success: null,
      attempt: null,
      attemptStatus: null,
      status: "partial",
      notes: [],
      raw: source,
    },
    new AbortController().signal,
  );
  expect(payload.observations.map((o) => o.objectKey)).toEqual([
    aftersalesObjectKey("report", "overview", "all"),
    aftersalesObjectKey("report", "category", "帽子"),
    null,
    null,
  ]);
  const counts = centerCounts(payload.observations, payload.actions, [
    { domain: "aftersales", status: "observed" },
  ]);
  expect(counts.exceptionCount).toBe(2);
  expect(counts.unmappedObservations).toBe(2);
  expect(payload.actions).toEqual([]);
  // 当前真实接口的金额/正式风险待核分支不能把空占位数组包装成可评估零。
  priorities = [];
  decisionStatus = "pending_validation";
  const pending = await aftersalesSpec.load(
    {
      id: "aftersales",
      version: "v",
      date: "2026-10-27",
      completeThrough: null,
      success: null,
      attempt: null,
      attemptStatus: null,
      status: "partial",
      notes: [],
      raw: source,
    },
    new AbortController().signal,
  );
  expect(pending.observationState).toBe("pending_validation");
  expect(pending.card.pairs.find((p) => p.label === "观察异常对象")?.value).toBeNull();
  expect(pending.card.heading).toBe("退货分析看板");
  const coverage = observationCoverage([
    { id: "aftersales", data: pending, error: null, loading: false },
  ]);
  expect(centerCounts(pending.observations, pending.actions, coverage).exceptionCount).toBeNull();
});

it("推广未完成证据校验的空清单为待核，已有真实观察不会因整体状态待核而丢失", async () => {
  let hasObservation = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () => ({
        data: path.includes("/diagnosis?")
          ? {
              current_window: { start: "2026-10-21", end: "2026-10-27" },
              previous_window: { start: "2026-10-14", end: "2026-10-20" },
              flagged_products: hasObservation
                ? [
                    {
                      object_key: "listing",
                      object_type: "product",
                      label: "真实观察对象",
                      listing_id: "listing",
                      product_id: "product",
                      product_name: null,
                      shop_id: "shop",
                      shop_name: null,
                      category: null,
                      level: null,
                      operator: null,
                      focus: false,
                      current: {},
                      previous: {},
                      findings: [
                        {
                          level: "观察",
                          rule: "有花费无成交，归因完整性待核实",
                          evidence: "花费300，成交0笔",
                          action: "核查归因",
                          status: "attribution_pending",
                        },
                      ],
                      clues: [],
                      notes: [],
                      priority: "观察",
                      evaluation_state: "partial",
                      rule_results: [],
                      exclusions: {
                        current: {
                          missing_spend_rows: 0,
                          missing_revenue_rows: 0,
                          revenue_without_spend: "0",
                        },
                        previous: {
                          missing_spend_rows: 0,
                          missing_revenue_rows: 0,
                          revenue_without_spend: "0",
                        },
                      },
                      current_days: 7,
                      previous_days: 7,
                      current_record_count: "7",
                      previous_record_count: "7",
                    },
                  ]
                : [],
              rule_version: "sop-test",
              status: "insufficient_evidence",
            }
          : { metrics: {} },
        meta: { data_version: "v" },
      }),
    })),
  );
  const meta = {
    id: "promotion" as const,
    version: "v",
    date: "2026-10-27",
    completeThrough: null,
    success: null,
    attempt: null,
    attemptStatus: null,
    status: "unknown",
    notes: [],
    raw: {
      mode: "product" as const,
      date_from: "2026-08-29",
      date_to: "2026-10-27",
      recommended_from: "2026-10-21",
      recommended_to: "2026-10-27",
      observed_rows: "14",
      complete_through: null,
      completeness: "unknown",
      sources: [],
    },
  };
  const pending = mergeDiagnostics(
    await promotionSpec.load(meta, new AbortController().signal),
    await promotionSpec.loadDiagnostics!(meta, new AbortController().signal),
  );
  expect(pending.observationState).toBe("pending_validation");
  hasObservation = true;
  const observed = mergeDiagnostics(
    await promotionSpec.load(meta, new AbortController().signal),
    await promotionSpec.loadDiagnostics!(meta, new AbortController().signal),
  );
  expect(observed.observationState).toBe("observed");
  expect(observed.observations).toHaveLength(1);
  expect(observed.observations[0].level).toBe("observation");
  expect(observed.observations[0].sourcePriority).toBe("观察");
  expect(observed.observations[0].ruleVersion).toBe("sop-test");
  expect(observed.observations[0].sourceRules).toEqual(["有花费无成交，归因完整性待核实"]);
  expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes("detail=alerts"))).toBe(
    true,
  );
  expect(observed.actions[0]).toMatchObject({
    domain: "promotion",
    objectKey: '["listing","shop","listing"]',
    suggestion: "核查归因",
    link: "/promotion?mode=product&product_from=2026-10-27&product_to=2026-10-27&product_shop=shop&product_products=listing#promotion-diagnosis",
  });
});
