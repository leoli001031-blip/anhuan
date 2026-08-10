import { Alert } from "antd";

export default function P4BoundaryBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      message="BUSINESS_SNAPSHOT_ONLY / NOT_SIGNED / NOT_PUBLISHED / NOT_PRODUCTION"
      description="仅用于内部业务快照与合成数据流程，不构成专业签发或对外发布。"
      style={{ marginBottom: 20 }}
    />
  );
}
