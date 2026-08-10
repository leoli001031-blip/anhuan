import { useEffect, useMemo, useState } from "react";
import { Alert, Empty, Pagination, Space, Spin, Typography } from "antd";
import { getPageTextUnit, userFacingIngestionError } from "../ingestionApi";
import type { PageText, PreviewUnit } from "../types";

interface PageTextPreviewProps {
  token: string | null;
  versionId: string;
  units: PreviewUnit[];
}

const MAX_RENDERED_PAGE_CHARACTERS = 100_000;

export default function PageTextPreview({
  token,
  versionId,
  units,
}: PageTextPreviewProps) {
  const ordered = useMemo(
    () => [...units].sort((left, right) => left.ordinal - right.ordinal),
    [units],
  );
  const [page, setPage] = useState(1);
  const [payload, setPayload] = useState<PageText | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unitIds = ordered.map((unit) => unit.id).join(":");

  useEffect(() => {
    setPage(1);
    setPayload(null);
  }, [unitIds, versionId]);

  const selected = ordered[Math.min(page - 1, ordered.length - 1)];
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setPayload(null);
    setLoading(true);
    setError(null);
    void getPageTextUnit(token, versionId, selected.id, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setPayload(next);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [selected, token, versionId]);

  if (ordered.length === 0) return <Empty description="没有可显示的文本页" />;

  const fullText = payload?.lines.join("\n") ?? "";
  const displayText = fullText.slice(0, MAX_RENDERED_PAGE_CHARACTERS);
  const clientTruncated = fullText.length > MAX_RENDERED_PAGE_CHARACTERS;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Text>{selected.label || `第 ${page} 页`}</Typography.Text>
        <Pagination
          simple
          current={page}
          pageSize={1}
          total={ordered.length}
          onChange={setPage}
          showSizeChanger={false}
        />
      </Space>
      {(payload?.truncated || clientTruncated) && (
        <Alert type="info" showIcon message="本页文本已按安全资源限制截断" />
      )}
      {error ? (
        <Alert type="error" showIcon message="文本预览读取失败" description={error} />
      ) : loading ? (
        <div style={{ minHeight: 220, display: "grid", placeItems: "center" }}>
          <Spin tip="正在读取受限文本预览" />
        </div>
      ) : (
        <div
          style={{
            maxHeight: "68vh",
            overflow: "auto",
            padding: 16,
            border: "1px solid #f0f0f0",
            borderRadius: 8,
            background: "#fafafa",
          }}
        >
          <Typography.Paragraph
            style={{
              margin: 0,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              lineHeight: 1.75,
            }}
          >
            {displayText || "本页没有可显示文本"}
          </Typography.Paragraph>
        </div>
      )}
    </Space>
  );
}
