// 运营台 · 共享材料（/console/shared-materials）：服务商共享域，独立管理。
import { Typography } from "antd";
import MaterialPanel from "../../components/MaterialPanel";

export default function SharedMaterialsPage() {
  return (
    <div style={{ maxWidth: 960 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        共享材料
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        共享材料对所有客户报告生效，请谨慎上传。
      </Typography.Paragraph>
      <MaterialPanel scope="shared" />
    </div>
  );
}
