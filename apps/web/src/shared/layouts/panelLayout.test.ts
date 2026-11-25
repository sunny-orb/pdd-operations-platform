// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { startPanelSession, updatePanel, useDashboardPanel } from "./panelLayout";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it("浏览器刷新保留两侧开合和宽度，关闭标签页后重新进入恢复默认", () => {
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
  act(() => {
    end = startPanelSession();
  });
  expect(panels.result.current).toEqual({
    directory: { open: false, width: 248 },
    assistant: { open: true, width: 520 },
  });
  window.dispatchEvent(new Event("pagehide"));
  end();
  // 关闭标签页后，新标签页没有上一页的 sessionStorage。
  sessionStorage.clear();
  act(() => {
    end = startPanelSession();
  });
  expect(panels.result.current).toEqual({
    directory: { open: true, width: 192 },
    assistant: { open: false, width: 440 },
  });
  end();
});

it("从浏览器往返缓存恢复时保留面板状态", () => {
  const end = startPanelSession();
  const panel = renderHook(() => useDashboardPanel("directory"));
  act(() => {
    updatePanel("directory", { open: false, width: 224 });
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  expect(panel.result.current).toEqual({ open: false, width: 224 });
  end();
});

it("存储写入失败后从往返缓存返回，不用旧快照覆盖当页最新布局", () => {
  const end = startPanelSession();
  const panel = renderHook(() => useDashboardPanel("directory"));
  act(() => updatePanel("directory", { open: false, width: 224 }));
  const save = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("存储已满", "QuotaExceededError");
  });
  try {
    act(() => {
      updatePanel("directory", { open: true, width: 248 });
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    });
    expect(panel.result.current).toEqual({ open: true, width: 248 });
  } finally {
    save.mockRestore();
    end();
  }
});
