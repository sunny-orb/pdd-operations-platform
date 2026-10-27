// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSalesData } from "./queries";
import { getSalesFilters, getSalesResult } from "./salesApi";
import { testFilters, testMeta, testMetrics, testShops } from "../testFixtures";

vi.mock("./salesApi", () => ({ getSalesFilters: vi.fn(), getSalesResult: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("updates same-version source status and clears recovered polling errors without fetching facts", async () => {
  const initial = {
    ...testFilters,
    data: {
      ...testFilters.data,
      sources: {
        shop_ratings: {
          state: "observed",
          observed_from: null,
          observed_through: "2026-10-26",
          source_updated_text: null,
          last_attempt_status: "succeeded",
          last_attempt_at: "2026-10-28T00:00:00Z",
          last_success_at: "2026-10-28T00:00:00Z",
        },
      },
    },
  };
  vi.mocked(getSalesFilters).mockResolvedValue(initial);
  vi.mocked(getSalesResult).mockResolvedValue({
    missingShopDays: 0,
    performance: testMetrics,
    shops: testShops,
    meta: testMeta,
  });
  const { result } = renderHook(() => useSalesData());
  await waitFor(() => expect(result.current.result).not.toBeNull());
  vi.mocked(getSalesFilters).mockRejectedValueOnce(new Error("offline"));
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await waitFor(() => expect(result.current.error).toContain("自动检查"));
  const failed = {
    ...initial,
    meta: { ...testMeta, last_attempt_status: "failed", last_attempt_at: "2026-10-28T02:00:00Z" },
    data: {
      ...initial.data,
      sources: {
        shop_ratings: {
          ...initial.data.sources.shop_ratings,
          last_attempt_status: "failed",
          last_attempt_at: "2026-10-28T02:00:00Z",
        },
      },
    },
  };
  vi.mocked(getSalesFilters).mockResolvedValue(failed);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await waitFor(() => expect(result.current.result?.meta.last_attempt_status).toBe("failed"));
  expect(result.current.filters?.data.sources?.shop_ratings.last_attempt_status).toBe("failed");
  expect(result.current.error).toBeNull();
  expect(getSalesResult).toHaveBeenCalledTimes(1);
});
