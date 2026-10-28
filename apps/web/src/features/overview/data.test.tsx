// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it } from "vitest";
import { useDomain } from "./data";
import type { DomainId, DomainMeta, DomainPayload } from "./model";
afterEach(cleanup);
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
  expect(result.current[0].error).toContain("销售查询失败");
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
      throw Object.assign(new Error("变版"), { status: 409 });
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
