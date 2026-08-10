import { useEffect, useState } from "react";
import { Alert, Card, Empty, Spin } from "antd";
import { getPreviewManifest, userFacingIngestionError } from "../ingestionApi";
import { reasonCopy, statusCopy } from "../reasonCopy";
import type { PreviewManifest, VersionSummary } from "../types";
import ImagePreview from "./ImagePreview";
import PageTextPreview from "./PageTextPreview";
import SpreadsheetPreview from "./SpreadsheetPreview";

interface PreviewPanelProps {
  token: string | null;
  version: VersionSummary;
}

function scanBlockCopy(version: VersionSummary): string {
  if (version.scan_status === "infected") return reasonCopy("MALWARE_DETECTED");
  if (version.scan_status === "unavailable") return reasonCopy("SCAN_ENGINE_UNAVAILABLE");
  if (version.scan_status === "error") return reasonCopy(version.reason_code);
  return `安全扫描状态：${statusCopy(version.scan_status)}`;
}

export default function PreviewPanel({ token, version }: PreviewPanelProps) {
  const [manifest, setManifest] = useState<PreviewManifest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setManifest(null);
    setError(null);
    if (version.scan_status !== "clean") {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void getPreviewManifest(token, version.id, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) setManifest(payload);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(userFacingIngestionError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, version.id, version.preview_status, version.scan_status, version.updated_at]);

  let content: React.ReactNode;
  if (version.scan_status !== "clean") {
    content = (
      <Alert
        type={version.scan_status === "infected" ? "error" : "warning"}
        showIcon
        message="预览仍被安全隔离"
        description={scanBlockCopy(version)}
      />
    );
  } else if (loading) {
    content = (
      <div style={{ minHeight: 260, display: "grid", placeItems: "center" }}>
        <Spin tip="正在准备安全预览" />
      </div>
    );
  } else if (error) {
    content = <Alert type="error" showIcon message="预览状态读取失败" description={error} />;
  } else if (!manifest) {
    content = <Empty description="暂时没有预览信息" />;
  } else if (manifest.status !== "ready") {
    content = (
      <Alert
        type={manifest.status === "failed" ? "error" : "info"}
        showIcon
        message={manifest.status === "generating" ? "正在生成安全预览" : "预览尚不可用"}
        description={manifest.reason_code ? reasonCopy(manifest.reason_code) : undefined}
      />
    );
  } else if (manifest.kind === "page_text") {
    content = (
      <PageTextPreview token={token} versionId={version.id} units={manifest.units} />
    );
  } else if (manifest.kind === "sheet_grid") {
    content = (
      <SpreadsheetPreview token={token} versionId={version.id} units={manifest.units} />
    );
  } else if (manifest.units[0]) {
    content = <ImagePreview token={token} versionId={version.id} unit={manifest.units[0]} />;
  } else {
    content = <Empty description="安全预览没有可显示内容" />;
  }

  return (
    <Card title="安全预览" styles={{ body: { minHeight: 180 } }}>
      {content}
    </Card>
  );
}
