// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Envelope, Score, Scores } from "../api/contracts";
import { buildDefaultFilters } from "../state/filters";
import { ScoresSection } from "./ScoresSection";

const query = vi.hoisted(() => vi.fn());
vi.mock("../api/queries", () => ({ useProductQuery: query }));
vi.mock("../components/SectionState", () => ({
  SectionState: ({
    query: result,
    children,
  }: {
    query: { data?: Envelope<unknown> };
    children: (data: unknown) => unknown;
  }) => (result.data?.data ? children(result.data.data) : null),
}));
vi.mock("../components/FiltersBar", () => ({ FiltersBar: () => <div>独立评分筛选</div> }));

const score: Score = {
  id: "sample-score",
  product: {
    key: '["sample-shop","sample-id"]',
    shopId: "sample-shop",
    shopName: "示例官方店",
    id: "sample-id",
    name: "示例晴日帽",
    styleCode: "S01",
    category: "帽子",
    level: "新品",
    operator: "示例运营",
  },
  date: "2026-10-14",
  modelVersion: "preview-v1",
  stage: "观察期",
  age: 8,
  season: "秋季",
  status: "partial",
  score: null,
  verifiedScore: 48,
  pendingWeight: 30,
  recommendation: "暂不放量",
  reason: "推广窗口数据不足",
  metrics: [
    {
      key: "uv",
      label: "近7日访客",
      value: "500",
      format: "number",
      weight: 10,
      tiers: [100, 300, 600, 900],
      grade: "良好",
      points: 5,
      reference: "示例帽子参照组",
      sampleSize: 20,
      reason: "",
    },
  ],
};
const envelope = <T,>(data: T): Envelope<T> => ({
  data,
  availability: "ready",
  computationState: "ready",
  meta: {
    dataVersion: "preview-v1",
    dataThrough: "2026-10-14",
    lastSuccessAt: null,
    completeness: "complete",
    missingReasons: [],
    isPreview: true,
  },
});

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
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
});
afterEach(() => {
  cleanup();
  query.mockReset();
});

function mount() {
  query.mockImplementation((endpoint: string) => ({
    data: envelope(
      endpoint.startsWith("scores/")
        ? score
        : ({
            items: [score],
            total: 1,
            counts: { complete: 0, partial: 1, unscored: 0 },
          } satisfies Scores),
    ),
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }));
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ScoresSection
        mode="preview"
        scenario="normal"
        filters={buildDefaultFilters()}
        onChange={vi.fn()}
        options={null}
        versionToken={0}
      />
    </QueryClientProvider>,
  );
}

describe("新品评分展示", () => {
  it("待验证卡只显示已验证得分，不将缺项折算成完整分数", () => {
    mount();
    expect(screen.getByText("示例晴日帽")).toBeTruthy();
    expect(screen.getAllByText(/已验证得分/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/完整评分 48/)).toBeNull();
    expect(screen.getByText("推广窗口数据不足")).toBeTruthy();
  });

  it("点击查看档位后按评分ID读取详情，并展示指标阈值", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /查看.*档位/ }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(query.mock.calls.some(([endpoint]) => endpoint === "scores/sample-score")).toBe(true);
    expect(screen.getByText("近7日访客")).toBeTruthy();
    expect(screen.getByText("示例帽子参照组")).toBeTruthy();
  });
});
