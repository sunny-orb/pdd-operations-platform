import { Anchor, Button, Grid, Tooltip, Typography } from "antd";
import { MenuFoldOutlined, MenuUnfoldOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import "./dashboard-sections.css";

export interface DashboardSectionLink {
  id: string;
  title: string;
  icon?: ReactNode;
}

const directoryWidth = { initial: 192, min: 176, max: 280, step: 8 };
const clampWidth = (width: number) =>
  Math.max(directoryWidth.min, Math.min(directoryWidth.max, width));

/** 复用看板页内目录；调用方提供稳定区块ID，正文负责保留对应锚点。 */
export function DashboardSections({
  sections,
  children,
  header,
  desktopTargetOffset = 24,
}: {
  sections: readonly DashboardSectionLink[];
  children: ReactNode;
  header?: ReactNode;
  desktopTargetOffset?: number;
}) {
  const screens = Grid.useBreakpoint();
  const [collapsed, setCollapsed] = useState(false);
  const directoryId = useId();
  const [width, setWidth] = useState(directoryWidth.initial);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ pointer: number; start: number; width: number } | null>(null);
  const style = { "--directory-width": `${width}px` } as CSSProperties;
  return (
    <div
      className={`dashboard-sections${collapsed ? " is-collapsed" : ""}${resizing ? " is-resizing" : ""}`}
      style={style}
    >
      <aside className="dashboard-sections-sidebar">
        <nav className="dashboard-sections-directory" aria-label="本页目录">
          <div className="dashboard-sections-heading">
            <Button
              type="text"
              className="dashboard-sections-toggle"
              aria-label={collapsed ? "展开本页目录" : "收起本页目录"}
              aria-expanded={!collapsed}
              aria-controls={directoryId}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
            <Typography.Text strong>本页目录</Typography.Text>
            <Typography.Text type="secondary">{sections.length} 个区块</Typography.Text>
          </div>
          <div id={directoryId} hidden={!screens.lg && collapsed}>
            <Anchor
              affix={false}
              showInkInFixed
              replace
              direction={screens.lg ? "vertical" : "horizontal"}
              targetOffset={screens.lg ? desktopTargetOffset : 112}
              items={sections.map(({ id, title, icon }) => ({
                key: id,
                href: `#${id}`,
                title: (
                  <Tooltip title={screens.lg && collapsed ? title : undefined} placement="right">
                    <span className="dashboard-sections-link" aria-label={title}>
                      <span className="dashboard-sections-icon" aria-hidden>
                        {icon ?? <UnorderedListOutlined />}
                      </span>
                      <span className="dashboard-sections-label">{title}</span>
                    </span>
                  </Tooltip>
                ),
              }))}
            />
          </div>
        </nav>
      </aside>
      <div
        className="dashboard-sections-resizer"
        role="separator"
        aria-label="调整目录宽度"
        aria-orientation="vertical"
        aria-valuemin={directoryWidth.min}
        aria-valuemax={directoryWidth.max}
        aria-valuenow={width}
        aria-valuetext={`${width}像素`}
        hidden={!screens.lg || collapsed}
        tabIndex={0}
        onDoubleClick={() => setWidth(directoryWidth.initial)}
        onKeyDown={(event) => {
          const next =
            event.key === "ArrowLeft"
              ? width - directoryWidth.step
              : event.key === "ArrowRight"
                ? width + directoryWidth.step
                : event.key === "Home"
                  ? directoryWidth.min
                  : event.key === "End"
                    ? directoryWidth.max
                    : null;
          if (next !== null) {
            event.preventDefault();
            setWidth(clampWidth(next));
          }
        }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { pointer: event.pointerId, start: event.clientX, width };
          setResizing(true);
        }}
        onPointerMove={(event) => {
          const active = drag.current;
          if (active?.pointer === event.pointerId)
            setWidth(clampWidth(active.width + event.clientX - active.start));
        }}
        onPointerUp={(event) => {
          if (drag.current?.pointer !== event.pointerId) return;
          event.currentTarget.releasePointerCapture(event.pointerId);
          drag.current = null;
          setResizing(false);
        }}
        onLostPointerCapture={() => {
          drag.current = null;
          setResizing(false);
        }}
        onPointerCancel={() => {
          drag.current = null;
          setResizing(false);
        }}
      />
      <div className="dashboard-sections-content">
        {header}
        {children}
      </div>
    </div>
  );
}
