// @vitest-environment jsdom
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePublishedVersions } from "./publishedVersions";
import { usePublishedDomainRefresh } from "./usePublishedDomainRefresh";
vi.mock("./publishedVersions", () => ({ usePublishedVersions: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("初查不重复读取，只有该域发布变化触发刷新，使用当前回调", () => {
  const result = { data: { products: "p1", promotion: "m1", sales: "s1", aftersales: "a1" } };
  vi.mocked(usePublishedVersions).mockReturnValue(
    result as ReturnType<typeof usePublishedVersions>,
  );
  const refresh = vi.fn();
  const hook = renderHook(({ callback }) => usePublishedDomainRefresh("products", callback), {
    initialProps: { callback: refresh },
  });
  expect(refresh).not.toHaveBeenCalled();
  result.data.sales = "s2";
  hook.rerender({ callback: refresh });
  expect(refresh).not.toHaveBeenCalled();
  const next = vi.fn();
  result.data.products = "p2";
  hook.rerender({ callback: next });
  expect(next).toHaveBeenCalledTimes(1);
  hook.rerender({ callback: next });
  expect(next).toHaveBeenCalledTimes(1);
});
