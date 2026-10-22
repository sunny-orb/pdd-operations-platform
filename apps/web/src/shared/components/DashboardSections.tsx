import { Anchor, Grid, Typography } from "antd";
import { UnorderedListOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
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
}: {
  sections: readonly DashboardSectionLink[];
  children: ReactNode;
}) {
  const screens = Grid.useBreakpoint();
  return (
    <div className="dashboard-sections">
      <aside className="dashboard-sections-sidebar">
        <nav className="dashboard-sections-directory" aria-label="本页目录">
          <div className="dashboard-sections-heading">
            <UnorderedListOutlined aria-hidden />
            <Typography.Text strong>本页目录</Typography.Text>
            <Typography.Text type="secondary">{sections.length} 个区块</Typography.Text>
          </div>
          <Anchor
            affix={false}
            showInkInFixed
            replace
            direction={screens.lg ? "vertical" : "horizontal"}
            targetOffset={screens.lg ? 24 : 112}
            items={sections.map(({ id, title, icon }) => ({
              key: id,
              href: `#${id}`,
              title: (
                <span className="dashboard-sections-link">
                  {icon && <span aria-hidden>{icon}</span>}
                  <span>{title}</span>
                </span>
              ),
            }))}
          />
        </nav>
      </aside>
      <div className="dashboard-sections-content">{children}</div>
    </div>
  );
}
