// 本地合成数据标记：仅 mock 模式渲染，固定于视口右下角。
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
      本地合成数据
    </div>
  );
}
