import { Descriptions, Empty, Tag, Typography } from "antd";
import { formatP4Bytes, formatP4DateTime } from "../reasonCopy";
import type { ReportArtifactMetadata } from "../types";

interface Props {
  artifact: ReportArtifactMetadata | null | undefined;
}

export default function ArtifactMetadata({ artifact }: Props) {
  if (!artifact) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 artifact 元数据" />;
  return (
    <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
      <Descriptions.Item label="Artifact 类型">
        <Tag color="blue">{artifact.artifact_kind}</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="存储形态">{artifact.storage_kind}</Descriptions.Item>
      <Descriptions.Item label="Content-Type">{artifact.content_type}</Descriptions.Item>
      <Descriptions.Item label="状态">
        <Tag color="green">{artifact.status}</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="大小">{formatP4Bytes(artifact.size_bytes)}</Descriptions.Item>
      <Descriptions.Item label="生成时间">{formatP4DateTime(artifact.created_at)}</Descriptions.Item>
      <Descriptions.Item label="SHA-256" span={2}>
        <Typography.Text code style={{ overflowWrap: "anywhere" }}>
          {artifact.sha256}
        </Typography.Text>
      </Descriptions.Item>
    </Descriptions>
  );
}
