import { describe, expect, it } from "vitest";
import { parseApiEnvelope } from "./wire";
import type { Overview, Scores, Score } from "./contracts";

export const syntheticMeta = {
  data_version: "synthetic.v1",
  input_batches: { product_daily: "synthetic" },
  profile_basis: "current",
  data_through: "2026-10-22",
  complete_through: null,
  last_success_at: "2026-10-23T00:00:00Z",
  sync_status: "succeeded",
  last_error: null,
  completeness: "unknown",
  missing_reasons: ["source_completeness_unknown"],
  source_updated_at: null,
  source_update_status: "not_recorded_for_current_publication",
  is_preview: false,
};
export const response = (data: unknown) => ({
  data,
  meta: syntheticMeta,
  availability: "ready",
  computation_state: "ready",
});
const metrics = [
  "product_revenue",
  "visitors",
  "favorites",
  "buyers",
  "units",
  "revenue_per_buyer",
  "buyer_conversion_rate",
].map((key) => ({
  key,
  label: key,
  value: key === "favorites" ? null : "9007199254740993.123456789",
  unit: key.includes("rate") ? "ratio" : key.includes("revenue") ? "yuan" : "count",
  previous: "0",
  year_ago: null,
  mom_change: null,
  yoy_change: null,
  status: key === "favorites" ? "missing" : "valid",
  missing_reasons: key === "favorites" ? ["definition_pending"] : [],
  missing_value_count: 0,
  observed_record_count: 1,
}));
export const syntheticOverview = {
  metrics,
  periods: [],
  funnel: [],
  health: { status: "insufficient_evidence", title: "来源完整性待补充", description: "已观察数据" },
  matched_record_count: 1,
  windows: { current: { start_date: "2026-10-22", end_date: "2026-10-22" } },
  coverage: {},
};
export const syntheticCard = {
  score_id: "synthetic-score",
  platform_code: "pdd",
  shop_id: "s",
  product_id: "10000000000000000001",
  product_name: "合成商品",
  shop_name: "合成店铺",
  category: null,
  operator: null,
  profile_match_status: "conflict",
  field_statuses: { category: "conflict" },
  as_of_date: "2026-10-22",
  score_status: "partial",
  verified_score: "62.5",
  complete_score: null,
  verified_weight: 76,
  pending_weight: 24,
  active_signal_count: 8,
  pending_signal_count: 2,
  met_signal_count: 8,
  decision: {
    code: "pending_validation",
    label: "先补验证 · 暂不放量",
    reasons: ["source_completeness_unknown"],
    execution_note: null,
  },
  current_values: { product_revenue_7d: "18242.23", visitors_7d: "3450" },
  ad_impressions_7d: "32216",
  ad_spend_7d: "1.23456789",
  model_category: "羽绒裤",
  season: "平季",
  lifecycle: "放量期",
  reference_context_basis: "fixed_published_reference",
  threshold_status: "fixed_display_trial",
};

describe("正式API校验及展示映射", () => {
  it("周期小计保留独立覆盖，单日全局覆盖不能证明双7日完整", () => {
    const coverage = {
      expected_days: 7,
      observed_days: 1,
      not_imported_dates: ["2026-08-23"],
      missing_record_dates: [],
      missing_product_dates: [],
    };
    const data = {
      ...syntheticOverview,
      periods: [
        {
          days: 7,
          value: "269791.93",
          mom_change: null,
          window: { start_date: "2026-08-18", end_date: "2026-08-24" },
          status: "partial",
          missing_reasons: ["outside_imported_range"],
          coverage,
          previous_coverage: { ...coverage, observed_days: 0 },
        },
      ],
    };
    const v = parseApiEnvelope<Overview>("overview", response(data));
    expect(v.data!.periods[0]).toMatchObject({
      status: "partial",
      missingReasons: ["部分日期尚未入库"],
      coverage: { expectedDays: 7, observedDays: 1 },
    });
  });
  it("模型标识和规则版本分别保留，不将规则误称模型", () => {
    const detail = {
      ...syntheticCard,
      model_id: "synthetic-model",
      rule_version: "synthetic-rule",
      threshold_provenance: {},
      inputs: {},
      context: {
        listed_date: null,
        age_days: null,
        first_product_observed_date: null,
        age_basis: "unknown",
        lifecycle_status: "not_classified",
        threshold_precision: "display_rounded",
        evidence_reasons: [],
        current_window: { start_date: "2026-10-16", end_date: "2026-10-22" },
        previous_window: { start_date: "2026-10-09", end_date: "2026-10-15" },
        coverage: {},
      },
      signals: Array.from({ length: 10 }, (_, i) => ({
        metric_key: `metric${i}`,
        label: "合成指标",
        unit: "yuan",
        weight: 10,
        current_value: "1",
        points: "2.5",
        tier: "basic",
        status: "valid",
        missing_reasons: [],
        reference_group: null,
        reference_sample_count: null,
        fallback_level: null,
        thresholds: { basic: "1", good: "2", excellent: "3", outstanding: "4" },
      })),
    };
    const v = parseApiEnvelope<Score>("scores/synthetic-score", response(detail));
    expect(v.data!.modelVersion).toBe("synthetic-model");
    expect(v.data).toHaveProperty("ruleVersion", "synthetic-rule");
  });
  it("保留相容结果时仍映射最近失败尝试，不能只看ready", () => {
    const v = parseApiEnvelope("overview", {
      ...response(syntheticOverview),
      meta: {
        ...syntheticMeta,
        last_attempt_status: "failed",
        last_attempt_at: "2026-10-23T01:00:00Z",
        last_error: { code: "SCORE_FAILED", message: "合成失败" },
      },
    });
    expect(v.meta).toHaveProperty("lastAttemptStatus", "failed");
  });
  it("snake_case与十进制原串正确接入，NULL/原因不丢失", () => {
    const v = parseApiEnvelope<Overview>("overview", response(syntheticOverview));
    expect(v.data!.metrics[0].value).toBe("9007199254740993.123456789");
    expect(v.data!.metrics[2].value).toBeNull();
    expect(v.data!.metrics[2].missingReasons).toContain("收藏日值定义待确认");
    expect(v.meta.dataThrough).toBe("2026-10-22");
    expect(v.meta.completeThrough).toBeNull();
  });
  it("评分列表不假造详情、实际阶段或缺失日期，保留试算标记", () => {
    const data = {
      items: [syntheticCard],
      total: 1,
      page: 1,
      page_size: 25,
      as_of_date: "2026-10-22",
      summary: {
        total_count: 1,
        filtered_count: 1,
        scored_count: 1,
        complete_count: 0,
        partial_count: 1,
        unscored_count: 0,
      },
    };
    const v = parseApiEnvelope<Scores>("scores", {
      ...response(data),
      meta: {
        ...syntheticMeta,
        score_version: "score.v1",
        as_of_date: "2026-10-22",
        has_compatible_result: true,
        model_status: "fixed_display_trial",
        computed_at: "2026-10-23T00:00:00Z",
      },
    });
    const score = v.data!.items[0];
    expect(score.verifiedScore).toBe("62.5");
    expect(score).toHaveProperty("activeCount", 8);
    expect(score).toHaveProperty("pendingCount", 2);
    expect(score).toHaveProperty("metCount", 8);
    expect(score.age).toBeNull();
    expect(score.stage).toContain("历史参照");
    expect(score.metrics).toEqual([]);
    expect(score.product.category).toBe("资料冲突");
    expect(v.meta.scoreVersion).toBe("score.v1");
  });
  it("拒绝浮点金额、残缺结构及样例冒充正式数据", () => {
    const bad = structuredClone(syntheticOverview);
    bad.metrics[0].value = 42 as unknown as string;
    expect(() => parseApiEnvelope("overview", response(bad))).toThrow("格式");
    expect(() => parseApiEnvelope("scores", response({ items: [] }))).toThrow("格式");
    expect(() =>
      parseApiEnvelope("overview", {
        ...response(syntheticOverview),
        meta: { ...syntheticMeta, is_preview: true },
      }),
    ).toThrow("格式");
  });
  it("待生成不是空结果或尚未接库", () => {
    const v = parseApiEnvelope<Scores>("scores", {
      ...response({
        items: [],
        total: 0,
        page: 1,
        page_size: 25,
        as_of_date: "2026-10-22",
        summary: {
          total_count: 148,
          filtered_count: 0,
          scored_count: 0,
          complete_count: 0,
          partial_count: 0,
          unscored_count: 0,
        },
      }),
      availability: "not_connected",
      computation_state: "pending",
      meta: { ...syntheticMeta, has_compatible_result: false },
    });
    expect(v.computationState).toBe("pending");
  });
});
