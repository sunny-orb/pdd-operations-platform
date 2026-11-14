// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Filters } from "./Filters";
import { readScope, patchScope, type ScopeName, type Scope } from "./scope";
import type { ProductsState } from "./data";
const probe = vi.hoisted(() => ({ menus: [] as { open: boolean; count: number }[] }));
vi.mock("antd", async (original) => {
  const actual = await original<typeof import("antd")>();
  return {
    ...actual,
    Select: (props: import("antd").SelectProps) => {
      const id = String(props["aria-label"]);
      if (id === "总览商品" || id === "明细商品")
        probe.menus.push({ open: !!props.open, count: props.options?.length ?? 0 });
      return <actual.Select {...props} />;
    },
  };
});
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  probe.menus = [];
});
afterEach(() => {
  cleanup();
  clients.forEach((c) => c.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});
function Host({ name }: { name: "main" | "detail" }) {
  const [query, setQuery] = useSearchParams();
  const scope = readScope(query, name, "2026-10-01", "2026-10-31");
  const state = {
    dateSelectionKey: () => "fixed-dates",
    version: "v1",
    metadata: { data: { date_from: scope.from, date_to: scope.to } },
    change: (name: ScopeName, patch: Partial<Scope>) =>
      setQuery((q) => patchScope(q, name, patch, scope.from, scope.to), { replace: true }),
    stale() {},
    reset() {},
  } as unknown as ProductsState;
  return <Filters name={name} scope={scope} state={state} />;
}
it.each(["main", "detail"] as const)(
  "%s首次自动展开只挂载新候选，继续搜索不收起且清空可关闭",
  async (name) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    clients.push(client);
    client.setQueryData(["m1", "catalog", "main", "v1", "2026-10-01", "2026-10-31"], {
      shops: [{ value: "s1", label: "甲店" }],
      total: 2,
      items: ["小糖豆帽", "其他商品"].map((label, i) => ({
        value: String(i),
        label,
        search_text: label,
        shop: "s1",
        operator: null,
        category: null,
        level: null,
      })),
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Host name={name} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const input = screen.getByRole("textbox", {
      name: name === "main" ? "总览搜索商品" : "明细搜索商品",
    });
    const chooser = screen.getByRole("combobox", {
      name: name === "main" ? "总览商品" : "明细商品",
    });
    input.focus();
    fireEvent.change(input, { target: { value: "小糖豆" } });
    probe.menus = [];
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    fireEvent.keyUp(input, { key: "Enter", keyCode: 13 });
    await act(async () => {});
    const opened = probe.menus.filter((item) => item.open).map((item) => item.count);
    expect(opened).toContain(1);
    expect(opened).not.toContain(2);
    expect(document.activeElement).toBe(input);
    probe.menus = [];
    fireEvent.change(input, { target: { value: "其他" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    fireEvent.keyUp(input, { key: "Enter", keyCode: 13 });
    await act(async () => {});
    expect(probe.menus.length).toBeGreaterThan(0);
    expect(probe.menus.every((item) => item.open)).toBe(true);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(chooser.getAttribute("aria-expanded")).toBe("false");
    fireEvent.change(input, { target: { value: "小糖豆" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });
    fireEvent.keyUp(input, { key: "Enter", keyCode: 13 });
    await act(async () => {});
    expect(chooser.getAttribute("aria-expanded")).toBe("true");
    fireEvent.change(input, { target: { value: "" } });
    await act(async () => {});
    expect(chooser.getAttribute("aria-expanded")).toBe("false");
  },
);
