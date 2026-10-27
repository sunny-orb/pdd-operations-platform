/** 五个看板的站内入口；不包含业务数据、查询条件或外部来源地址。 */
export const dashboardPages = [
  { id: "overview", path: "/", title: "运营看板中心" },
  { id: "sales", path: "/sales", title: "店铺销售" },
  { id: "products", path: "/products", title: "商品经营" },
  { id: "promotion", path: "/promotion", title: "推广分析" },
  { id: "aftersales", path: "/aftersales", title: "售后分析" },
] as const;

export type DashboardPageId = (typeof dashboardPages)[number]["id"];
