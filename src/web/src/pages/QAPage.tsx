import { useState } from "react";
import { Button, Card, Input, List, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { api, getSelectedEnterprise } from "../api";

interface Citation {
  chunk_id: string;
  document_id: string;
  pages: number[];
  snippet: string;
}

interface QaResult {
  answer: string | null;
  citations: Citation[];
  refusal_reason: string | null;
  request_id: string | null;
}

export default function QAPage() {
  const { getAccessToken } = useAuth();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QaResult | null>(null);
  const [loading, setLoading] = useState(false);

  const ask = async () => {
    setLoading(true);
    try {
      const body: Record<string, string> = { question };
      const eid = getSelectedEnterprise();
      if (eid) {
        body.enterprise_id = eid;
      }
      const resp = await api<QaResult>("/v1/qa", {
        method: "POST",
        token: getAccessToken(),
        body,
      });
      setResult(resp);
    } catch (e) {
      setResult({
        answer: null,
        citations: [],
        refusal_reason: String(e),
        request_id: null,
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <Typography.Title level={4}>证据化问答</Typography.Title>
      <Input.TextArea
        rows={3}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="输入问题，例如：废气治理采用什么方案？"
      />
      <Button
        type="primary"
        loading={loading}
        onClick={ask}
        style={{ marginTop: 12 }}
      >
        提问
      </Button>
      {result && (
        <Card style={{ marginTop: 16 }}>
          {result.refusal_reason ? (
            <Typography.Text type="warning">
              拒答：{result.refusal_reason}
            </Typography.Text>
          ) : (
            <>
              <Typography.Paragraph>{result.answer}</Typography.Paragraph>
              <List
                size="small"
                header="引用"
                dataSource={result.citations}
                renderItem={(c) => (
                  <List.Item>
                    <Typography.Text code>{c.chunk_id}</Typography.Text>
                    <Typography.Text type="secondary">
                      {" "}
                      pages: {c.pages.join(",")}
                    </Typography.Text>
                  </List.Item>
                )}
              />
            </>
          )}
        </Card>
      )}
    </div>
  );
}
