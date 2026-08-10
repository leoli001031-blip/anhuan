import { Alert } from "antd";

export default function P7BoundaryBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      message="LOCAL_REHEARSAL_ONLY / MANUAL_EXECUTION / NO_PRODUCTION_ACCESS / NO_DEPLOYMENT / NOT_PRODUCTION"
      description="本阶段只记录本地人工计划、人工检查结果与回滚门，不执行 Shell、Docker、恢复、部署或生产动作。"
      style={{ marginBottom: 20 }}
    />
  );
}
