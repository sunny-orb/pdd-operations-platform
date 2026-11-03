import {
  memo,
  cloneElement,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

/** 单值筛选的开合交互；只取消弹层和鼠标焦点，不清空业务条件。 */
export const FilterField = memo(function FilterField({
  label,
  children,
}: {
  label: string;
  children: ReactElement<{ open: boolean; onOpenChange: (open: boolean) => void }>;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLLabelElement>(null);
  const pointer = useRef(false);
  const dismissClick = useRef(false);

  useEffect(() => {
    const onPointer = () => {
      pointer.current = true;
    };
    const onKeyboard = () => {
      pointer.current = false;
    };
    document.addEventListener("pointerdown", onPointer, true);
    document.addEventListener("keydown", onKeyboard, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("keydown", onKeyboard, true);
    };
  }, []);

  const onOpenChange = useCallback((next: boolean) => {
    setOpen(next);
    const active = document.activeElement;
    // 鼠标关闭时清除高亮；键盘关闭仍保留焦点，便于继续输入和Tab切换。
    if (
      !next &&
      pointer.current &&
      active instanceof HTMLElement &&
      root.current?.contains(active)
    ) {
      active.blur();
    }
  }, []);

  // cloneElement只传递事件回调；不会在渲染时执行回调或读取ref。
  const control = cloneElement(children, { open, onOpenChange });

  return (
    <label
      ref={root}
      onMouseDownCapture={(event) => {
        // 弹层通过portal渲染；点候选、日历翻月不能被当成再点输入框。
        if (event.button !== 0 || !event.currentTarget.contains(event.target as Node)) return;
        dismissClick.current = open;
        if (open) {
          event.preventDefault();
          event.stopPropagation();
          onOpenChange(false);
        }
      }}
      onClickCapture={(event) => {
        if (dismissClick.current && event.currentTarget.contains(event.target as Node)) {
          dismissClick.current = false;
          event.preventDefault();
          event.stopPropagation();
        }
      }}
    >
      {label}
      {control}
    </label>
  );
});
