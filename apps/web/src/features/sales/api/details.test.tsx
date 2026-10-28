// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { z } from "zod";
import { getJson } from "../../../shared/api/client";
import { useSalesDetail } from "./details";

vi.mock("../../../shared/api/client", async (original) => ({
  ...(await original<object>()),
  getJson: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("同版本目标失败及恢复时按轻量元信息更新说明", async () => {
  const schema = z.object({ meta: z.object({ data_version: z.string() }), note: z.string() });
  const onConflict = vi.fn();
  vi.mocked(getJson).mockResolvedValue({ meta: { data_version: "v" }, note: "成功" });
  const { result, rerender } = renderHook(
    ({ statusKey }) =>
      useSalesDetail("/api/v1/sales/targets?data_version=v", schema, onConflict, statusKey),
    { initialProps: { statusKey: "success-1" } },
  );
  await waitFor(() => expect(result.current.data?.note).toBe("成功"));
  vi.mocked(getJson).mockResolvedValue({ meta: { data_version: "v" }, note: "失败，保留" });
  act(() => rerender({ statusKey: "failure-2" }));
  await waitFor(() => expect(result.current.data?.note).toBe("失败，保留"));
  vi.mocked(getJson).mockResolvedValue({ meta: { data_version: "v" }, note: "恢复成功" });
  act(() => rerender({ statusKey: "success-3" }));
  await waitFor(() => expect(result.current.data?.note).toBe("恢复成功"));
  expect(onConflict).not.toHaveBeenCalled();
  expect(getJson).toHaveBeenCalledTimes(3);
});
