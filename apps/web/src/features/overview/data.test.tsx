// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/client";
import { useDomain } from "./data";
import {
  observationCoverage,
  buildCenterView,
  type DomainId,
  type DomainMeta,
  type DomainPayload,
} from "./model";
vi.mock("../../shared/api/publishedVersions", () => ({
  waitForPublicationBaseline: async () => {},
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const payload = (value: string): DomainPayload => ({
  card: {
    title: "合成域",
    heading: "合成",
    value,
    valueType: "currency",
    valueLabel: "观察值",
    pairs: [],
    note: "",
    link: "/products",
    status: "partial",
  },
  observations: [],
  observationState: "observed",
  actions: [],
});
const meta = (id: DomainId, version = "v1", attemptStatus = "succeeded"): DomainMeta => ({
  id,
  version,
  date: "2026-10-27",
  completeThrough: null,
  success: null,
  attempt: null,
  attemptStatus,
  status: "partial",
  notes: [],
  raw: null,
});
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}
it("一个负责域失败不阻断其他三域真实结果", async () => {
  const { wrapper } = setup();
  const spec = (id: DomainId) => ({
    id,
    meta: async () => {
      if (id === "sales") throw new Error("销售查询失败");
      return meta(id);
    },
    load: async () => payload(id),
  });
  const specs = [spec("sales"), spec("products"), spec("promotion"), spec("aftersales")];
  const { result } = renderHook(
    () => [useDomain(specs[0]), useDomain(specs[1]), useDomain(specs[2]), useDomain(specs[3])],
    { wrapper },
  );
  await waitFor(() => expect(result.current[3].data?.card.value).toBe("aftersales"));
  expect(result.current[0].error).toContain("读取失败");
  expect(result.current.slice(1).map((r) => r.data?.card.value)).toEqual([
    "products",
    "promotion",
    "aftersales",
  ]);
});
it("同版来源尝试更新不重拉事实，变版取消旧请求并拒绝旧值覆盖", async () => {
  const { client, wrapper } = setup();
  let version = "v1",
    status = "succeeded",
    calls = 0;
  const pending: { version: string; signal: AbortSignal; done: (value: DomainPayload) => void }[] =
    [];
  const spec = {
    id: "products" as const,
    meta: async () => meta("products", version, status),
    load: async (m: DomainMeta, signal: AbortSignal) => {
      calls++;
      return new Promise<DomainPayload>((done) =>
        pending.push({ version: m.version, signal, done }),
      );
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(pending.length).toBe(1));
  status = "failed";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "products", "meta"] });
  });
  await waitFor(() => expect(result.current.meta?.attemptStatus).toBe("failed"));
  expect(calls).toBe(1);
  version = "v2";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "products", "meta"] });
  });
  await waitFor(() => expect(pending.length).toBe(2));
  expect(pending[0].signal.aborted).toBe(true);
  await act(async () => {
    pending[1].done(payload("2"));
  });
  await waitFor(() => expect(result.current.data?.card.value).toBe("2"));
  await act(async () => {
    pending[0].done(payload("999"));
  });
  expect(result.current.data?.card.value).toBe("2");
});
it("409每个版本只自动重查一次，持续冲突不无限刷新", async () => {
  const { wrapper } = setup();
  let calls = 0;
  const spec = {
    id: "products" as const,
    meta: async () => {
      calls++;
      return meta("products");
    },
    load: async () => {
      throw new ApiError("变版", 409);
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.error).toContain("变版"));
  await waitFor(() => expect(calls).toBe(2));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
  expect(calls).toBe(2);
});
it("同版经营月及日历推进重查事实，来源尝试更新仍只改状态", async () => {
  const { client, wrapper } = setup();
  let scope = "2026-10:2026-10-31";
  let calls = 0;
  const spec = {
    id: "sales" as const,
    meta: async () => ({ ...meta("sales"), scopeKey: scope }),
    load: async (m: DomainMeta) => {
      calls++;
      return payload(m.scopeKey!);
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.value).toBe(scope));
  scope = "2026-11:2026-11-01";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "sales", "meta"] });
  });
  await waitFor(() => expect(result.current.data?.card.value).toBe(scope));
  scope = "2026-11:2026-11-02";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "sales", "meta"] });
  });
  await waitFor(() => expect(result.current.data?.card.value).toBe(scope));
  expect(calls).toBe(3);
});

it("同版503瞬时失败自动恢复，不等发布版本变化", async () => {
  const { wrapper } = setup();
  let calls = 0;
  const spec = {
    id: "products" as const,
    meta: async () => meta("products"),
    load: async () => {
      calls++;
      if (calls === 1) throw new ApiError("暂时失败", 503);
      return payload("恢复值");
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.value).toBe("恢复值"), { timeout: 2500 });
  expect(calls).toBe(2);
});
it("停留45秒及窗口聚焦不会重新读取域元信息", async () => {
  vi.useFakeTimers();
  const { wrapper } = setup();
  const readMeta = vi.fn(async () => meta("products"));
  const spec = { id: "products" as const, meta: readMeta, load: async () => payload("1") };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(result.current.data?.card.value).toBe("1");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(90_000);
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(readMeta).toHaveBeenCalledTimes(1);
  focusManager.setFocused(undefined);
});
it("独立诊断失败保留基础指标，观察覆盖为失败且不显示零异常", async () => {
  const { wrapper } = setup();
  const spec = {
    id: "products" as const,
    meta: async () => meta("products"),
    load: async () => ({ ...payload("123"), observationState: "not_available" as const }),
    loadDiagnostics: async () => {
      throw new ApiError("诊断读取失败", 422);
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.value).toBe("123"));
  await waitFor(() => expect(result.current.error).toContain("风险与建议读取失败"));
  expect(result.current.data?.observationState).toBe("not_available");
  const coverage = observationCoverage([result.current]);
  expect(coverage).toEqual([{ domain: "products", status: "failed" }]);
  expect(buildCenterView([], [], coverage).actionCount).toBeNull();
});
it("新版诊断不能并入旧版保留指标，旧请求卸载取消", async () => {
  const { client, wrapper } = setup();
  let version = "v1";
  const pending: AbortSignal[] = [];
  const spec = {
    id: "products" as const,
    meta: async () => meta("products", version),
    load: async (m: DomainMeta, signal: AbortSignal) => {
      if (m.version === "v1") return payload("旧指标");
      pending.push(signal);
      return new Promise<DomainPayload>(() => {});
    },
    loadDiagnostics: async () => ({
      observations: [],
      actions: [],
      observationState: "observed" as const,
    }),
  };
  const { result, unmount } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.value).toBe("旧指标"));
  version = "v2";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "products", "meta"] });
  });
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(result.current.data?.card.value).toBe("旧指标");
  unmount();
  expect(pending[0].aborted).toBe(true);
});

it("诊断503最多重试两次，手动重试同版恢复且基础指标不丢失", async () => {
  const { wrapper } = setup();
  let failing = true;
  let diagnosticCalls = 0;
  const spec = {
    id: "products" as const,
    meta: async () => meta("products"),
    load: async () => ({ ...payload("123"), observationState: "not_available" as const }),
    loadDiagnostics: async () => {
      diagnosticCalls++;
      if (failing) throw new ApiError("稍后重试", 503);
      return { observations: [], actions: [], observationState: "observed" as const };
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.value).toBe("123"));
  await waitFor(() => expect(result.current.error).toContain("风险与建议"), { timeout: 4500 });
  expect(diagnosticCalls).toBe(3);
  expect(observationCoverage([result.current])[0].status).toBe("failed");
  failing = false;
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.data?.observationState).toBe("observed"));
  expect(result.current.error).toBeNull();
  expect(result.current.data?.card.value).toBe("123");
  expect(diagnosticCalls).toBe(4);
});
it("跨版本先到诊断不并入旧指标，新指标到达才合并同版观察", async () => {
  const { client, wrapper } = setup();
  let version = "v1";
  let finish: ((value: DomainPayload) => void) | undefined;
  const spec = {
    id: "products" as const,
    meta: async () => meta("products", version),
    load: async (m: DomainMeta) =>
      m.version === "v1"
        ? payload("1")
        : new Promise<DomainPayload>((resolve) => {
            finish = resolve;
          }),
    loadDiagnostics: async (m: DomainMeta) => ({
      observations: [],
      actions: [],
      observationState: "observed" as const,
      cardPatch: { note: `诊断${m.version}` },
    }),
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(result.current.data?.card.note).toContain("诊断v1"));
  version = "v2";
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "products", "meta"] });
  });
  await waitFor(() => expect(finish).toBeDefined());
  expect(result.current.data?.card.value).toBe("1");
  expect(result.current.data?.card.note).toContain("诊断v1");
  expect(result.current.meta?.version).toBe("v1");
  await act(async () => {
    finish!(payload("2"));
  });
  await waitFor(() => expect(result.current.data?.card.value).toBe("2"));
  expect(result.current.data?.card.note).toContain("诊断v2");
  expect(result.current.meta?.version).toBe("v2");
});

it("同版诊断刷新失败保留旧观察并标记保留，恢复成功后才重新标已读取", async () => {
  const { client, wrapper } = setup();
  let failing = false;
  const spec = {
    id: "products" as const,
    meta: async () => meta("products"),
    load: async () => payload("123"),
    loadDiagnostics: async () => {
      if (failing) throw new ApiError("诊断失败", 422);
      return { observations: [], actions: [], observationState: "observed" as const };
    },
  };
  const { result } = renderHook(() => useDomain(spec), { wrapper });
  await waitFor(() => expect(observationCoverage([result.current])[0].status).toBe("observed"));
  failing = true;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["overview", "products", "diagnostics"] });
  });
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(result.current.data?.card.value).toBe("123");
  expect(observationCoverage([result.current])[0].status).toBe("retained");
  failing = false;
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toBeNull());
  expect(observationCoverage([result.current])[0].status).toBe("observed");
});
