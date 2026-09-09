import { useEffect, useState } from "react";
import { Alert, Button, Spin } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { getNativeExtractionStatus } from "../features/p3/ingestionApi";
import type { NativeExtractionStatus, VersionSummary } from "../features/p3/types";
import NativeRecoveryButton from './NativeRecoveryButton';

export default function NativeEvidenceStatus({ version }: { version: VersionSummary }) {
  const { getAccessToken } = useAuth();
  const label = version.content_type === "image/jpeg" ? "图片"
    : version.content_type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ? "Excel" : "DOCX";
  const unit = label === "Excel" ? "单元格" : label === "图片" ? "图像" : "正文块";
  const [result, setResult] = useState<NativeExtractionStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setFailed(false);
    getNativeExtractionStatus(getAccessToken(), version.id, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.schema !== "anhuan-native-extraction-v1" || value.version_id !== version.id) {
        setFailed(true);
        return;
      }
      setResult(value);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [getAccessToken, version.id, version.updated_at, nonce]);

  useEffect(() => {
    if (!result || !["pending", "running", "retry_wait"].includes(result.state)) return;
    const timer = window.setTimeout(() => setNonce((value) => value + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [result]);

  if (failed) return <Alert type="error" message={`暂时无法读取 ${label} 提取状态`} action={<Button onClick={() => setNonce((value) => value + 1)}>重试</Button>} />;
  if (!result) return <Spin tip={`读取 ${label} 提取状态`}><div style={{ minHeight: 48 }} /></Spin>;
  if (result.effective?.state === 'revoked') return <Alert type="warning" message="当前人工确认已撤销" description="此材料已退出新问答和新报告的证据范围。已生成报告保留原引用。" />;
  if (result.effective?.state === 'ready') return <Alert type="success" message={result.effective.evidence_kind === 'review' ? '人工修订已生效' : `${label} 原生提取完成`} description={`当前 ${result.effective.fragment_count} 个内容片段可用于新问答和报告引用。报告生成进度以报告列表为准。`} />;
  if (result.effective?.state === 'unavailable') return <Alert type="warning" message="当前版本尚不可引用" description="材料尚未释放、已被替代或来源状态已变化，请检查当前材料版本。" />;
  if (result.state === "disabled") return <Alert type="info" message={`${label} 原生提取尚未启用`} />;
  if (result.state === "not_registered") return <Alert type="info" message={`${label} 尚未进入原生提取`} description={version.quarantine_status === "released" ? "尚无提取任务，可以由当前管理员补投。" : "完成安全检查并释放材料后，将开始提取内容。"} action={version.quarantine_status==='released' && <NativeRecoveryButton versionId={version.id} jobId={null} onDone={()=>setNonce(x=>x+1)} />} />;
  if (result.state === "blocked") return <Alert type="warning" message={`${label} 提取已停止`} description={result.reason_code === "NATIVE_OCR_UNAVAILABLE" ? "图片识别服务暂不可用，请检查识别配置后恢复任务。" : "此材料尚不可用于问答或报告。解决文件或权限问题后，当前管理员可以恢复任务。"} action={<NativeRecoveryButton versionId={version.id} jobId={result.job_id} onDone={()=>setNonce(x=>x+1)} />} />;
  if (result.state !== "done") return <Alert type="info" message={result.state === "retry_wait" ? `${label} 提取等待重试` : `${label} 正在提取`} />;
  if (result.coverage_state === "partial") return <Alert type="warning" message={`${label} 提取不完整`} description={`已检查 ${result.processed_block_count ?? 0} / ${result.expected_block_count ?? 0} 个${unit}，仍有内容未完整提取。此材料不可用于问答或报告。`} />;
  return <Alert type="info" message={result.report_source_eligible ? `${label} 原生提取完成` : `${label} 没有可用内容`} description={result.report_source_eligible ? `已保存 ${result.fragment_count ?? 0} 个内容片段。此材料暂不可用于问答或报告。` : "请检查原文件是否包含可提取的正文。"} />;
}
