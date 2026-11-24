import { useRef } from "react";
import { panelWidths, updatePanel } from "../layouts/panelLayout";
import "./panel-resizer.css";

/** 左右栏共用分隔线，方向键和指针均按屏幕坐标移动边界。 */
export function PanelResizer({
  panel,
  width,
  label,
  controls,
  hidden,
}: {
  panel: keyof typeof panelWidths;
  width: number;
  label: string;
  controls: string;
  hidden?: boolean;
}) {
  const drag = useRef<{ pointer: number; x: number; width: number } | null>(null);
  const limits = panelWidths[panel];
  const direction = panel === "directory" ? 1 : -1;
  const setWidth = (width: number) => updatePanel(panel, { width });
  return (
    <div
      className={`panel-resizer panel-resizer--${panel}`}
      role="separator"
      aria-label={label}
      aria-controls={controls}
      aria-orientation="vertical"
      aria-valuemin={limits.min}
      aria-valuemax={limits.max}
      aria-valuenow={width}
      aria-valuetext={`${width}像素`}
      tabIndex={0}
      hidden={hidden}
      title={`${label}（拖动或使用方向键，双击恢复默认）`}
      onDoubleClick={() => setWidth(limits.initial)}
      onKeyDown={(event) => {
        const values: Record<string, number> = {
          ArrowLeft: width - direction * 8,
          ArrowRight: width + direction * 8,
          Home: limits.min,
          End: limits.max,
        };
        if (Object.hasOwn(values, event.key)) {
          event.preventDefault();
          setWidth(values[event.key]);
        }
      }}
      onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointer: event.pointerId, x: event.clientX, width };
        event.currentTarget.dataset.dragging = "true";
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (start?.pointer === event.pointerId)
          setWidth(start.width + direction * (event.clientX - start.x));
      }}
      onPointerUp={(event) => {
        if (drag.current?.pointer === event.pointerId)
          event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onLostPointerCapture={(event) => {
        drag.current = null;
        delete event.currentTarget.dataset.dragging;
      }}
    />
  );
}
