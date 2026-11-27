/** 五个看板的站内入口；不包含业务数据、查询条件或外部来源地址。 */
export const dashboardPages = [
  { id: "overview", path: "/", title: "运营看板中心", tabTitle: "运营中心看板" },
  { id: "sales", path: "/sales", title: "店铺销售", tabTitle: "店铺销售看板" },
  { id: "products", path: "/products", title: "商品数据", tabTitle: "商品数据看板" },
  { id: "promotion", path: "/promotion", title: "推广分析", tabTitle: "推广分析看板" },
  { id: "aftersales", path: "/aftersales", title: "售后分析", tabTitle: "售后分析看板" },
] as const;

export type DashboardPageId = (typeof dashboardPages)[number]["id"];
