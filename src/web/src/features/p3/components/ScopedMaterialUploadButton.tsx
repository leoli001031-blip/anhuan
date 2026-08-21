import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Typography } from "antd";
import { ENTERPRISE_CHANGED_EVENT, getSelectedEnterprise } from "../../../api";
import { useAuth } from "../../../auth/OidcProvider";
import {
  getIngestionCapabilities,
  listIngestionDocuments,
} from "../ingestionApi";
import type {
  IngestionCapabilities,
  KnowledgeScopeTarget,
  MaterialKind,
} from "../types";
import BatchDocumentUploadModal from "./BatchDocumentUploadModal";

interface Props {
  knowledgeScope: KnowledgeScopeTarget;
  defaultMaterialKind?: MaterialKind;
  label: string;
  scopeHint?: string;
  type?: "default" | "primary";
  onComplete?: () => void;
}

type Availability = "checking" | "ready" | "denied" | "failed";

export default function ScopedMaterialUploadButton({
  knowledgeScope,
  defaultMaterialKind = "unknown",
  label,
  scopeHint,
  type = "default",
  onComplete,
}: Props) {
  const { getAccessToken } = useAuth();
  const [availability, setAvailability] = useState<Availability>("checking");
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [open, setOpen] = useState(false);
  const activeProbe = useRef<AbortController | null>(null);

  const probe = useCallback(async () => {
    activeProbe.current?.abort();
    setOpen(false);
    setCapabilities(null);
    if (!getSelectedEnterprise()) {
      setAvailability("denied");
      return;
    }
    const controller = new AbortController();
    activeProbe.current = controller;
    setAvailability("checking");
    try {
      const [nextCapabilities, collection] = await Promise.all([
        getIngestionCapabilities(getAccessToken(), controller.signal),
        listIngestionDocuments(getAccessToken(), { limit: 1 }, controller.signal),
      ]);
      if (!controller.signal.aborted) {
        setCapabilities(nextCapabilities);
        setAvailability(
          nextCapabilities.upload_enabled &&
            collection.allowed_actions.includes("create_document")
            ? "ready"
            : "denied",
        );
      }
    } catch {
      if (!controller.signal.aborted) setAvailability("failed");
    } finally {
      if (activeProbe.current === controller) activeProbe.current = null;
    }
  }, [getAccessToken]);

  useEffect(() => {
    void probe();
    const handleTenantChange = () => void probe();
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, handleTenantChange);
    return () => {
      window.removeEventListener(ENTERPRISE_CHANGED_EVENT, handleTenantChange);
      activeProbe.current?.abort();
    };
  }, [
    knowledgeScope.client_account_id,
    knowledgeScope.kind,
    probe,
  ]);

  if (availability === "failed") {
    return <Typography.Text type="secondary">材料上传权限暂不可用</Typography.Text>;
  }
  if (availability !== "ready" || !capabilities) return null;

  return (
    <>
      <Button type={type} onClick={() => setOpen(true)}>{label}</Button>
      <BatchDocumentUploadModal
        open={open}
        token={getAccessToken()}
        capabilities={capabilities}
        knowledgeScope={knowledgeScope}
        defaultMaterialKind={defaultMaterialKind}
        scopeHint={scopeHint}
        onCancel={() => setOpen(false)}
        onComplete={() => onComplete?.()}
      />
    </>
  );
}
