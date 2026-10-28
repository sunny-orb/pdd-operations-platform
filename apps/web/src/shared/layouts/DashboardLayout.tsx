import { App as AntApp, ConfigProvider } from "antd";
import { AppstoreOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import type { CSSProperties, ReactNode } from "react";
import { DashboardSections, type DashboardSectionLink } from "../components/DashboardSections";
import { dashboardPages, type DashboardPageId } from "./dashboardNavigation";
import zhCN from "antd/locale/zh_CN";
import { dashboardTheme } from "./dashboardTheme";
import "./dashboard-layout.css";

const headerHeight = 62;
const layoutStyle = { "--dashboard-header-height": `${headerHeight}px` } as CSSProperties;

/** 统一看板壳：业务页只提供当前页面、目录及正文，布局不读取业务接口。 */
export function DashboardLayout({
  currentPage,
  sections = [],
  context = "统一范围 · 本页独立分析",
  children,
}: {
  currentPage: DashboardPageId;
  sections?: readonly DashboardSectionLink[];
  context?: ReactNode;
  children: ReactNode;
}) {
  const header = (
    <header className="dashboard-topbar" aria-label="看板导航">
      <Link className="dashboard-brand" to="/" aria-label="返回运营看板中心">
        <AppstoreOutlined aria-hidden /> <span>拼多多运营平台</span>
      </Link>
      <nav className="dashboard-page-tabs" aria-label="看板切换">
        {dashboardPages.map((page) => (
          <Link
            key={page.id}
            to={page.path}
            aria-current={page.id === currentPage ? "page" : undefined}
          >
            {page.title}
          </Link>
        ))}
      </nav>
      {context && <span className="dashboard-topbar-context">{context}</span>}
    </header>
  );
  return (
    <ConfigProvider locale={zhCN} theme={dashboardTheme}>
      <AntApp>
        <div className="dashboard-layout" style={layoutStyle}>
          {sections.length ? (
            <DashboardSections
              sections={sections}
              header={header}
              desktopTargetOffset={headerHeight + 24}
            >
              {children}
            </DashboardSections>
          ) : (
            <>
              {header}
              {children}
            </>
          )}
        </div>
      </AntApp>
    </ConfigProvider>
  );
}
