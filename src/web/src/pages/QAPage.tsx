import { Typography } from "antd";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import MaterialQaPanel from "../features/material-rag/MaterialQaPanel";
import type { ClosedQueryId } from "../features/material-rag/types";
import { CLOSED_QUERY_IDS } from "../features/material-rag/types";

function isClosedQueryId(value: string | null): value is ClosedQueryId {
  return Boolean(value && (CLOSED_QUERY_IDS as readonly string[]).includes(value));
}

export default function QAPage() {
  const [params] = useSearchParams();
  const clientAccountId = params.get("client");
  const initialQueryId = useMemo(() => {
    const requested = params.get("query");
    if (isClosedQueryId(requested)) return requested;
    return clientAccountId ? "client.current" : "provider.shared";
  }, [clientAccountId, params]);

  return (
    <div style={{ maxWidth: 960 }}>
      <Typography.Title level={4}>公司知识问答</Typography.Title>
      <MaterialQaPanel clientAccountId={clientAccountId} initialQueryId={initialQueryId} />
    </div>
  );
}
