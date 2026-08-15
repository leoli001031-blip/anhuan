import { Alert, Typography } from "antd";

export default function QAPage() {
  return (
    <div style={{ maxWidth: 720 }}>
      <Typography.Title level={4}>公司知识问答</Typography.Title>
      <Alert
        type="warning"
        showIcon
        message="自由提问尚未开放"
        description="当前只在4份内部 Demo PDF 上验证脱敏向量索引和范围隔离；用户问题不会发送到外部向量服务。"
      />
    </div>
  );
}
