import { App as AntApp, ConfigProvider } from "antd";
import { AppstoreOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useEffect, type CSSProperties, type ReactNode } from "react";
import { DashboardSections, type DashboardSectionLink } from "../components/DashboardSections";
import { CalendarIcon } from "../components/CalendarIcon";
import { dashboardPages, type DashboardPageId } from "./dashboardNavigation";
import zhCN from "antd/locale/zh_CN";
import { dashboardTheme } from "./dashboardTheme";
import "./dashboard-layout.css";
import { AssistantEntry } from "../assistant/AssistantWorkspace";

const headerHeight = 62;
const layoutStyle = { "--dashboard-header-height": `${headerHeight}px` } as CSSProperties;
const datePickerConfig = { suffixIcon: <CalendarIcon /> };

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
  const pageTitle = dashboardPages.find((page) => page.id === currentPage)!.tabTitle;
  useEffect(() => {
    const previousTitle = document.title;
    document.title = pageTitle;
    return () => {
      document.title = previousTitle;
    };
  }, [pageTitle]);

  const header = (
    <header className="dashboard-topbar" aria-label="看板导航">
      <Link className="dashboard-brand" to="/" aria-label="返回运营看板中心">
        <AppstoreOutlined aria-hidden /> <span>拼多多运营平台</span>
      </Link>
      {context && <span className="dashboard-topbar-context">{context}</span>}
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
        <a
          href="https://stock-monitor.skills.kktree.cn/v2/#view=inventory&sort=sales7_market&order=desc"
          target="_blank"
          rel="noopener noreferrer"
        >
          库存监控
        </a>
      </nav>
      <AssistantEntry />
    </header>
  );
  return (
    <ConfigProvider locale={zhCN} theme={dashboardTheme} datePicker={datePickerConfig}>
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
