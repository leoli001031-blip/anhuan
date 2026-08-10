import { useEffect, useState } from "react";
import { Alert, Spin } from "antd";
import { getPreviewImageUnitBlob, userFacingIngestionError } from "../ingestionApi";
import type { PreviewUnit } from "../types";

interface ImagePreviewProps {
  token: string | null;
  versionId: string;
  unit: PreviewUnit;
}

export default function ImagePreview({ token, versionId, unit }: ImagePreviewProps) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let createdUrl: string | null = null;
    setImageUrl(null);
    setLoading(true);
    setError(null);
    void getPreviewImageUnitBlob(token, versionId, unit.id, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        createdUrl = URL.createObjectURL(blob);
        setImageUrl(createdUrl);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [token, unit.id, versionId]);

  if (error) return <Alert type="error" showIcon message="预览读取失败" description={error} />;
  if (loading) {
    return (
      <div style={{ minHeight: 240, display: "grid", placeItems: "center" }}>
        <Spin tip="正在读取安全预览" />
      </div>
    );
  }
  if (!imageUrl) return <Alert type="warning" showIcon message="预览内容为空" />;

  return (
    <div
      style={{
        maxHeight: "68vh",
        overflow: "auto",
        textAlign: "center",
        background: "#f5f5f5",
        borderRadius: 8,
        padding: 12,
      }}
    >
      <img
        src={imageUrl}
        alt="安全预览"
        style={{ maxWidth: "100%", height: "auto", display: "inline-block" }}
      />
    </div>
  );
}
