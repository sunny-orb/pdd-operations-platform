import type { ThemeConfig } from "antd";

/** 五页共用的组件库主题；业务专属布局留在各自样式中。 */
export const dashboardTheme: ThemeConfig = {
  token: {
    colorPrimary: "#1768d9",
    colorSuccess: "#118362",
    colorError: "#c33443",
    colorWarning: "#d78d17",
    colorText: "#142b49",
    colorTextSecondary: "#718097",
    colorBorderSecondary: "#e1e8f1",
    colorBgLayout: "#f5f7fb",
    borderRadius: 10,
    fontSize: 13,
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Card: { headerFontSize: 13 },
    Table: { headerBg: "#f5f8fc", cellPaddingBlockSM: 12 },
    Select: { controlHeight: 34 },
    Button: { controlHeight: 34 },
  },
};
