import { useSyncExternalStore } from "react";

export const panelWidths = {
  directory: { initial: 192, min: 176, max: 280 },
  assistant: { initial: 440, min: 320, max: 560 },
} as const;

type Panel = keyof typeof panelWidths;
type PanelState = { open: boolean; width: number };
type Layout = Record<Panel, PanelState>;
const defaults: Layout = {
  directory: { open: true, width: panelWidths.directory.initial },
  assistant: { open: false, width: panelWidths.assistant.initial },
};
const storageKey = "pdd.dashboard.panels.v1";
const listeners = new Set<() => void>();
let layout = defaults;
let storageDirty = false;

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function publish(next: Layout) {
  layout = next;
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify(layout));
    storageDirty = false;
  } catch {
    storageDirty = true;
    // 浏览器禁用存储时，仍允许在当前页面调整面板。
  }
  for (const notify of listeners) notify();
}

function boundedWidth(panel: Panel, width: number) {
  const { min, max } = panelWidths[panel];
  return Math.min(max, Math.max(min, Math.round(width)));
}

function restorePanel(panel: Panel, saved: unknown): PanelState {
  if (!saved || typeof saved !== "object") return defaults[panel];
  const { open, width } = saved as Partial<PanelState>;
  return {
    open: typeof open === "boolean" ? open : defaults[panel].open,
    width:
      typeof width === "number" && Number.isFinite(width)
        ? boundedWidth(panel, width)
        : defaults[panel].width,
  };
}

function readLayout(fallback = defaults): Layout {
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "null") as Partial<
      Record<Panel, unknown>
    > | null;
    return {
      directory: restorePanel("directory", saved?.directory),
      assistant: restorePanel("assistant", saved?.assistant),
    };
  } catch {
    return fallback;
  }
}

export function updatePanel(panel: Panel, patch: Partial<PanelState>) {
  const previous = layout[panel];
  const open = patch.open ?? previous.open;
  const width = boundedWidth(panel, patch.width ?? previous.width);
  if (open !== previous.open || width !== previous.width)
    publish({ ...layout, [panel]: { open, width } });
}

export function useDashboardPanel(panel: Panel) {
  return useSyncExternalStore(subscribe, () => layout[panel]);
}

/** 面板开合和宽度在当前标签页会话内共享；刷新保留，关闭标签页后恢复默认。 */
export function startPanelSession() {
  publish(readLayout());
  function resume(event: PageTransitionEvent) {
    if (event.persisted && !storageDirty) publish(readLayout(layout));
  }
  window.addEventListener("pageshow", resume);
  return () => window.removeEventListener("pageshow", resume);
}
