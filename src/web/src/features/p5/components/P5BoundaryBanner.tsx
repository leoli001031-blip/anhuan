import { Alert } from "antd";

export default function P5BoundaryBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      message="CANDIDATE_ONLY / INTERNAL_REVIEW_ONLY / NOT_LEGAL_ADVICE / PROFESSIONAL_JUDGMENT_REQUIRED / NOT_PRODUCTION"
      description="效力、适用范围与影响均为内部人工候选，必须由专业人员独立判断。"
      style={{ marginBottom: 20 }}
    />
  );
}
