// 演示环境标记：仅在显式 mock 标志（DEV + VITE_MATERIAL_RAG_REPORT_MOCK=1）下渲染。
// HTTP deterministic fixture 没有可靠环境标志——本组件在 HTTP 模式刻意不出现，
// 避免在无法证明的环境宣称「测试环境 · 演示数据」。
import { isMockData } from "../adapters";

export default function MockBadge() {
  if (!isMockData) return null;
  return (
    <div
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 1000,
        fontSize: 12,
        color: "var(--eco-warning)",
        border: "1px solid var(--eco-warning)",
        borderRadius: 4,
        padding: "2px 8px",
        background: "var(--eco-content-bg)",
      }}
    >
      测试环境 · 演示数据
    </div>
  );
}
