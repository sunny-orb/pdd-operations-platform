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
const listeners = new Set<() => void>();
let layout = defaults;

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function publish(next: Layout) {
  layout = next;
  for (const notify of listeners) notify();
}

function boundedWidth(panel: Panel, width: number) {
  const { min, max } = panelWidths[panel];
  return Math.min(max, Math.max(min, Math.round(width)));
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

/** 宽度和开合只在当前文档内共享。刷新、重新进入或从外部返回时恢复默认布局。 */
export function startPanelSession() {
  publish(defaults);
  function resume(event: PageTransitionEvent) {
    if (event.persisted) publish(defaults);
  }
  window.addEventListener("pageshow", resume);
  return () => window.removeEventListener("pageshow", resume);
}
