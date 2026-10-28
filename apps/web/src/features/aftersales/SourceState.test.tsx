// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SourceState } from "./SourceState";
afterEach(cleanup);
it("来源状态区分失败保留、无记录、口径待核和报告待重算", () => {
  render(
    <SourceState
      meta={{
        data_version: "v1",
        status: "partial",
        default_report_id: "r1",
        observed_to: "2026-10-27",
        complete_through: null,
        last_success_at: null,
        last_attempt_at: null,
        sources: ["failed_retained", "no_records", "observed", "new_input_pending_report"].map(
          (status) => ({
            code: status,
            label: status,
            locator: null,
            status,
            observed_from: null,
            observed_to: null,
            last_success_at: "2026-10-27T02:00:00Z",
            last_attempt_at: "2026-10-28T02:00:00Z",
            error_code: status === "failed_retained" ? "READ_FAILED" : null,
            note: "实际来源说明",
          }),
        ),
      }}
    />,
  );
  for (const label of [
    "最近接收失败，保留成功版本",
    "本报告无记录",
    "已接入，口径待核",
    "新输入已接收，报告待重算",
  ])
    expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText(/READ_FAILED/)).toBeTruthy();
});
