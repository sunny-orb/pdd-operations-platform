// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { startPanelSession, updatePanel, useDashboardPanel } from "./panelLayout";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it("浏览器刷新和重新进入均恢复两侧默认布局", () => {
  let type = "navigate";
  vi.stubGlobal("performance", { getEntriesByType: () => [{ type }], now: () => 0 });
  let end = startPanelSession();
  const panels = renderHook(() => ({
    directory: useDashboardPanel("directory"),
    assistant: useDashboardPanel("assistant"),
  }));
  act(() => {
    updatePanel("directory", { open: false, width: 248 });
    updatePanel("assistant", { open: true, width: 520 });
  });
  window.dispatchEvent(new Event("pagehide"));
  end();
  type = "reload";
  act(() => {
    end = startPanelSession();
  });
  expect(panels.result.current).toEqual({
    directory: { open: true, width: 192 },
    assistant: { open: false, width: 440 },
  });
  window.dispatchEvent(new Event("pagehide"));
  end();
  type = "navigate";
  act(() => {
    end = startPanelSession();
  });
  expect(panels.result.current).toEqual({
    directory: { open: true, width: 192 },
    assistant: { open: false, width: 440 },
  });
  end();
});
