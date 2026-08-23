// A-Eco 环保企业版 · 品牌色彩系统
// 所有颜色集中于此，未来替换甲方正式 VI 时只改这一个文件 + index.css 中的同名变量。
import type { ThemeConfig } from "antd";

export const eco = {
  primary: "#174D3A",
  primaryHover: "#2F7D61",
  primarySoft: "#EEF5F1",
  pageBackground: "#F5F7F4",
  contentBackground: "#FBFCFA",
  textPrimary: "#202824",
  textSecondary: "#66706A",
  border: "#DCE5DF",
  success: "#2F7D61",
  warning: "#A86605",
  danger: "#C7463A",
} as const;

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: eco.primary,
    colorInfo: eco.primary,
    colorLink: eco.primary,
    colorSuccess: eco.success,
    colorWarning: eco.warning,
    colorError: eco.danger,
    colorText: eco.textPrimary,
    colorTextSecondary: eco.textSecondary,
    colorBorder: eco.border,
    colorBorderSecondary: eco.border,
    colorBgLayout: eco.pageBackground,
    colorBgContainer: eco.contentBackground,
    colorPrimaryHover: eco.primaryHover,
    colorPrimaryBg: eco.primarySoft,
    borderRadius: 6,
    boxShadow: "none",
    fontFamily:
      'system-ui, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
  },
  components: {
    Layout: {
      headerBg: eco.contentBackground,
      headerHeight: 48,
      headerPadding: "0 24px",
      siderBg: eco.contentBackground,
      bodyBg: eco.pageBackground,
    },
    Menu: {
      itemSelectedColor: eco.primary,
      itemSelectedBg: "transparent",
      itemBg: "transparent",
      activeBarBorderWidth: 0,
    },
    Table: {
      headerBg: eco.contentBackground,
      headerSplitColor: "transparent",
      borderColor: eco.border,
    },
  },
};

// 报告正文/回答正文专用衬线（A-Eco 保留方向 A 的文档感）
export const serifFamily =
  '"Source Han Serif SC", "Noto Serif SC", "Songti SC", "SimSun", serif';
