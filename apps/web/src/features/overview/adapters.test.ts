import { afterEach, expect, it, vi } from "vitest";
import { aftersalesSpec } from "./adapters";
afterEach(() => vi.unstubAllGlobals());
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
