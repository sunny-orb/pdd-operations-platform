// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { ProductsPage } from "./ProductsPage";
import { requestProducts } from "./api/productsApi";
import { getPreviewResponse } from "./preview/provider";

vi.mock("./api/productsApi", () => ({ requestProducts: vi.fn() }));
beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {},
    })),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((el) => computed(el));
});
afterEach(() => {
  cleanup();
  vi.mocked(requestProducts).mockReset();
});

async function mount(path = "/products", scoreState: "ready" | "pending" | "failed" = "ready") {
  let releaseScore!: () => void;
  let releaseDays!: () => void;
  const daysGate = new Promise<void>((resolve) => {
    releaseDays = resolve;
  });
  const scoreGate = new Promise<void>((resolve) => {
    releaseScore = resolve;
  });
  vi.mocked(requestProducts).mockImplementation(async (endpoint, scope, filters) => {
    if (endpoint === "filters" && scope === "scores") await scoreGate;
    if (endpoint === "filters" && scope === "days") await daysGate;
    // 仅使用仓库已有合成输入驱动真实页面协调层，不调用正式数据。
    const result = await getPreviewResponse(endpoint, scope, filters, {
      mode: "preview",
      scenario: "normal",
    });
    return {
      ...result,
      ...(scope === "scores" ? { computationState: scoreState } : {}),
      ...(endpoint === "scores" && scoreState !== "ready" ? { data: null } : {}),
      meta: {
        ...result.meta,
        dataVersion: "perf-facts",
        scoreVersion: scope === "scores" ? "perf-score" : undefined,
      },
    };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ProductsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(vi.mocked(requestProducts).mock.calls.some(([endpoint]) => endpoint === "days")).toBe(
      true,
    ),
  );
  return { releaseScore, releaseDays };
}
it("默认首屏只查一次明细，等待评分版本后才查一次评分，并复用相同目录", async () => {
  const { releaseScore } = await mount();
  const calls = () => vi.mocked(requestProducts).mock.calls;
  expect(calls().filter(([endpoint]) => endpoint === "scores")).toHaveLength(0);
  expect(
    calls().filter(([endpoint, scope]) => endpoint === "filters" && scope === "days"),
  ).toHaveLength(0);
  expect(calls().filter(([endpoint]) => endpoint === "days")).toHaveLength(1);
  expect(calls().find(([endpoint]) => endpoint === "days")![2].dateFrom).not.toBe("");
  releaseScore();
  await waitFor(() =>
    expect(calls().filter(([endpoint]) => endpoint === "scores")).toHaveLength(1),
  );
  expect(calls().find(([endpoint]) => endpoint === "scores")![3].scoreVersion).toBe("perf-score");
});
it("独立明细条件不同仍查自身目录，使用推荐日期只发一次明细", async () => {
  const { releaseScore, releaseDays } = await mount("/products?days.keyword=不存在");
  const calls = vi.mocked(requestProducts).mock.calls;
  expect(
    calls.filter(([endpoint, scope]) => endpoint === "filters" && scope === "days"),
  ).toHaveLength(1);
  expect(calls.filter(([endpoint]) => endpoint === "days")).toHaveLength(1);
  expect(calls.find(([endpoint]) => endpoint === "days")![2].keyword).toBe("不存在");
  await act(async () => {
    releaseDays();
    releaseScore();
  });
  await waitFor(() => expect(calls.some(([endpoint]) => endpoint === "scores")).toBe(true));
  expect(calls.filter(([endpoint]) => endpoint === "days")).toHaveLength(1);
});
it.each(["pending", "failed"] as const)("API评分%s有版本时仍查询并显示状态", async (state) => {
  const { releaseScore } = await mount("/products", state);
  releaseScore();
  const section = screen.getByRole("region", { name: "查看表现档位与放量建议" });
  await within(section).findByText(state === "pending" ? "结果尚未生成" : "分析暂不可用");
  const calls = vi.mocked(requestProducts).mock.calls.filter(([endpoint]) => endpoint === "scores");
  expect(calls).toHaveLength(1);
  expect(calls[0][3].scoreVersion).toBe("perf-score");
});
