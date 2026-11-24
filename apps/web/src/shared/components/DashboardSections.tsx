import { Anchor, Button, Grid, Tooltip, Typography } from "antd";
import { MenuFoldOutlined, MenuUnfoldOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useId, type CSSProperties, type ReactNode } from "react";
import { PanelResizer } from "./PanelResizer";
import { useDashboardPanel, updatePanel } from "../layouts/panelLayout";
import "./dashboard-sections.css";

export interface DashboardSectionLink {
  id: string;
  title: string;
  icon?: ReactNode;
}

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
  const { open, width } = useDashboardPanel("directory");
  const collapsed = !open;
  const directoryId = useId();
  const style = { "--directory-width": `${width}px` } as CSSProperties;
  return (
    <div className={`dashboard-sections${collapsed ? " is-collapsed" : ""}`} style={style}>
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
              onClick={() => updatePanel("directory", { open: collapsed })}
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
      <PanelResizer
        panel="directory"
        width={width}
        label="调整目录宽度"
        controls={directoryId}
        hidden={!screens.lg || collapsed}
      />
      <div className="dashboard-sections-content">
        {header}
        {children}
      </div>
    </div>
  );
}
