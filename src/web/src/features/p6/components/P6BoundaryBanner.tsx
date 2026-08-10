import { Alert } from "antd";

export default function P6BoundaryBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      message="SYNTHETIC_ORACLE_ONLY / NON_GOLD / ACCURACY_NOT_EVALUATED / NO_EXTERNAL_MODEL_CALLS / NOT_PRODUCTION"
      description="所有结果仅来自有限结构化合成场景与本地确定性 Oracle，不代表真实准确率或专业质量。"
      style={{ marginBottom: 20 }}
    />
  );
}
