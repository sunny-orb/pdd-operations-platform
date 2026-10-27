import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ModulePage } from "../features/ModulePage";
import { getSystemStatus } from "../shared/api/system";

const ProductsPage = lazy(() =>
  import("../features/products/ProductsPage").then((module) => ({ default: module.ProductsPage })),
);

const SalesPage = lazy(() =>
  import("../features/sales/SalesPage").then((module) => ({ default: module.SalesPage })),
);

const PromotionPage = lazy(() =>
  import("../features/promotion/PromotionPage").then((module) => ({
    default: module.PromotionPage,
  })),
);

const modules = [
  {
    path: "/",
    title: "运营中心",
    description: "统一查看经营重点、数据状态与建议动作。",
    number: "01",
  },
  {
    path: "/sales",
    title: "店铺销售",
    description: "销售目标、店铺贡献与地区交易。",
    number: "02",
  },
  {
    path: "/products",
    title: "商品经营",
    description: "商品趋势、流量转化与新品潜力。",
    number: "03",
  },
  {
    path: "/promotion",
    title: "推广分析",
    description: "投放花费、归因交易与流量效率。",
    number: "04",
  },
  {
    path: "/aftersales",
    title: "售后分析",
    description: "退款趋势、原因结构与品类表现。",
    number: "05",
  },
];

/** 统一应用入口；无参数，展示基础导航与真实系统连接状态。 */
export function App() {
  const pathname = useLocation().pathname;
  const isProductPage = pathname === "/products";
  const isSalesPage = pathname === "/sales" || pathname === "/store-sales";
  const isPromotionPage = pathname === "/promotion";
  const status = useQuery({
    enabled: !isProductPage && !isSalesPage && !isPromotionPage,
    queryKey: ["system-status"],
    queryFn: ({ signal }) => getSystemStatus(signal),
  });
  if (isPromotionPage)
    return (
      <Suspense fallback={<p role="status">正在加载推广分析看板…</p>}>
        <PromotionPage />
      </Suspense>
    );
  if (isSalesPage)
    return (
      <Suspense fallback={<p role="status">正在加载店铺销售看板…</p>}>
        <SalesPage />
      </Suspense>
    );
  if (isProductPage)
    return (
      <Suspense fallback={<p role="status">正在加载商品看板…</p>}>
        <ProductsPage />
      </Suspense>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/">
          <span className="brand-icon">运</span>
          <span>
            拼多多运营平台<small>经营分析工作台</small>
          </span>
        </Link>
        <p className="nav-label">工作空间</p>
        <nav aria-label="业务导航">
          {modules.map((module) => (
            <NavLink end={module.path === "/"} to={module.path} key={module.path}>
              <span>{module.number}</span>
              {module.title}
              <b aria-hidden="true">↗</b>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="quiet-tag">初始化阶段</span>
          <p>
            先把数据接准，
            <br />
            再让经营一眼清楚。
          </p>
          <small>版本 0.1.0</small>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <span>
            工作空间 <span className="divider">/</span> 运营平台
          </span>
          <span className="quiet-tag">开发环境 · 尚未接入公司数据</span>
        </header>
        <div className="content">
          <section className="hero">
            <div>
              <span className="eyebrow">OPERATIONS WORKSPACE</span>
              <h1>从同一份可信数据出发</h1>
              <p>统一经营视图，让更新、口径和分析有据可查。</p>
            </div>
            <span className="phase-tag">P0 · 项目初始化</span>
          </section>
          <section className="connection-panel" aria-label="系统连接状态" aria-live="polite">
            <div className="connection-main">
              <span className={`status-dot ${status.isSuccess ? "connected" : ""}`} />
              <div>
                <strong>
                  {status.isPending
                    ? "正在检查服务"
                    : status.isError
                      ? "后端服务暂未连接"
                      : "后端服务已连接"}
                </strong>
                <p>
                  {status.isError
                    ? "请启动后端后重试。当前未展示任何经营结果。"
                    : "业务数据尚未接入，服务连通不代表数据已更新。"}
                </p>
              </div>
            </div>
            <div className="database-status">
              <small>应用数据库</small>
              <b>
                {status.data
                  ? status.data.databaseConfigured
                    ? "已配置 · 就绪待检查"
                    : "待配置"
                  : "待检查"}
              </b>
            </div>
            <button onClick={() => void status.refetch()} disabled={status.isFetching}>
              {status.isFetching ? "检查中…" : "刷新状态 ↻"}
            </button>
          </section>
          <Routes>
            <Route
              path="/"
              element={
                <>
                  <div className="section-heading">
                    <div>
                      <span className="eyebrow">BUSINESS MODULES</span>
                      <h2>一个入口，五个经营视角</h2>
                    </div>
                    <span>模块将分阶段接入</span>
                  </div>
                  <section className="module-grid">
                    {modules.slice(1).map((module) => (
                      <Link to={module.path} className="module-card" key={module.path}>
                        <div>
                          <span className="module-number">{module.number}</span>
                          <span className="quiet-tag">待接入</span>
                        </div>
                        <h3>{module.title}</h3>
                        <p>{module.description}</p>
                        <span className="card-link">
                          查看模块 <b>↗</b>
                        </span>
                      </Link>
                    ))}
                  </section>
                  <section className="next-step">
                    <div>
                      <span className="eyebrow">NEXT STEP</span>
                      <h2>先打通商品经营这一条链路</h2>
                      <p>确认数据契约，连接后端查询，再完成指标、趋势和明细的对账。</p>
                    </div>
                    <Link to="/products">进入商品模块 →</Link>
                  </section>
                </>
              }
            />
            {modules.slice(1).map((module) => (
              <Route
                path={module.path}
                key={module.path}
                element={<ModulePage title={module.title} description={module.description} />}
              />
            ))}
            <Route
              path="*"
              element={
                <section className="module-placeholder">
                  <h2>页面不存在</h2>
                  <Link to="/">返回运营中心</Link>
                </section>
              }
            />
          </Routes>
          <footer>
            数据完整截至：未接入 <span>·</span> 最近同步成功：尚无记录 <span>·</span>{" "}
            页面刷新不会触发上游同步
          </footer>
        </div>
      </main>
    </div>
  );
}
