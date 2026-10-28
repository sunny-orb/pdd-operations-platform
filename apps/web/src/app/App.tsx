import { lazy, Suspense } from "react";
import { Link, Route, Routes } from "react-router-dom";
const OverviewPage = lazy(() =>
  import("../features/overview/OverviewPage").then((module) => ({ default: module.OverviewPage })),
);
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
const AftersalesPage = lazy(() =>
  import("../features/aftersales/AftersalesPage").then((module) => ({
    default: module.AftersalesPage,
  })),
);
/** 五个独立看板共用导航；中心读取既有领域API，子页保持延迟加载。 */
export function App() {
  return (
    <Suspense fallback={<p role="status">正在加载经营看板…</p>}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/sales" element={<SalesPage />} />
        <Route path="/store-sales" element={<SalesPage />} />
        <Route path="/promotion" element={<PromotionPage />} />
        <Route path="/aftersales" element={<AftersalesPage />} />
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
    </Suspense>
  );
}
