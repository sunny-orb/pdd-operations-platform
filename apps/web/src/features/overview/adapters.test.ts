import { afterEach, expect, it, vi } from "vitest";
import { aftersalesSpec, salesSpec } from "./adapters";
import { aftersalesObjectKey, centerCounts } from "./model";
import { testFilters } from "../sales/testFixtures";
afterEach(() => vi.unstubAllGlobals());
it("基础目标同版本失败与恢复改变中心查询状态，避免保留旧成功说明", async () => {
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
            status: "partial",
            rate: null,
            amount: null,
            amount_share: null,
            change_pp: null,
            note: "待核",
          },
          priorities: [
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
          ],
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
});
