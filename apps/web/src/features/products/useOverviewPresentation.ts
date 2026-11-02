import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Board } from "./contracts";
import type { Scope } from "./scope";

type Snapshot = { data: Board; scope: Scope; version: string; key: string };

/** 总览新结果到达前保留同版本的上次完整展示；数值和它的筛选说明始终一起切换。
 * 调用方必须显示更新/失败状态，不能把保留结果解释为新筛选已完成。
 */
export function useOverviewPresentation(data: Board | undefined, scope: Scope, version: string) {
  const key = JSON.stringify(scope);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  if (data && (snapshot?.data !== data || snapshot.key !== key || snapshot.version !== version)) {
    setSnapshot({ data, scope, version, key });
  }
  const visible = snapshot?.version === version ? snapshot : null;
  return { data: data ?? visible?.data, scope: data ? scope : (visible?.scope ?? scope) };
}

/** 仅保持用户正在操作的总览筛选位置；主动滚动、离开筛选或调整视口后不再校正。 */
export function useFilterScrollAnchor(data: Board | undefined, settled: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const anchor = useRef<{ top: number; data: Board | undefined } | null>(null);
  const capture = () => {
    if (anchor.current) return;
    const rect = ref.current?.getBoundingClientRect();
    if (rect && rect.bottom > 0 && rect.top < window.innerHeight) {
      anchor.current = { top: rect.top, data };
    }
  };
  useEffect(() => {
    const cancel = () => {
      anchor.current = null;
    };
    const scroll = (event: Event) => {
      // 滚动候选列表不是浏览页面，仍需保持筛选框的位置。
      if (
        event.target instanceof Element &&
        event.target.closest(".ant-select-dropdown, .ant-picker-dropdown")
      )
        return;
      cancel();
    };
    const outside = (event: Event) => {
      const target = event.target;
      if (
        target instanceof Element &&
        !ref.current?.contains(target) &&
        !target.closest(".ant-select-dropdown, .ant-picker-dropdown")
      )
        cancel();
    };
    const key = (event: KeyboardEvent) => {
      if (
        ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key) &&
        !(
          event.target instanceof Element &&
          event.target.closest("input, textarea, [role=combobox]")
        )
      )
        cancel();
    };
    window.addEventListener("wheel", scroll, { passive: true });
    window.addEventListener("touchmove", scroll, { passive: true });
    window.addEventListener("resize", cancel);
    window.addEventListener("pointerdown", outside);
    window.addEventListener("focusin", outside);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("wheel", scroll);
      window.removeEventListener("touchmove", scroll);
      window.removeEventListener("resize", cancel);
      window.removeEventListener("pointerdown", outside);
      window.removeEventListener("focusin", outside);
      window.removeEventListener("keydown", key);
    };
  }, []);
  useLayoutEffect(() => {
    const current = anchor.current;
    if (!current || !ref.current) return;
    const delta = ref.current.getBoundingClientRect().top - current.top;
    if (Math.abs(delta) > 0.5) window.scrollBy({ top: delta, behavior: "instant" });
    // 防抖期间旧数据仍在，不能提前解除锚点；成功/失败完成后才结束这一轮。
    if (settled && (data !== current.data || !data)) anchor.current = null;
  }, [data, settled]);
  return { ref, capture };
}
