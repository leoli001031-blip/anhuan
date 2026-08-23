// 客户端智能问答：mock 走查保留五态；HTTP 模式问答未开放，
// 直达本页只给简洁说明与「查看分析报告」下一步，不渲染残废表单。
import { useState } from "react";
import { Button, Input, Skeleton, Typography } from "antd";
import { Link } from "react-router-dom";
import { isMockData, useApi } from "../../adapters";
import type { QaAnswer } from "../../adapters/types";
import ErrorState from "../../components/ErrorState";

type Phase =
  | { kind: "idle" }
  | { kind: "loading"; question: string }
  | { kind: "done"; result: QaAnswer }
  | { kind: "error"; error: unknown; question: string };

export default function QaPage() {
  const api = useApi();
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  if (!isMockData) {
    return (
      <div className="reading-column">
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          智能问答
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          问答能力正在接入，暂未开放。你可以先查看已发布的分析报告。
        </Typography.Paragraph>
        <Link to="/portal/reports">
          <Button type="primary">查看分析报告</Button>
        </Link>
      </div>
    );
  }

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setPhase({ kind: "loading", question: trimmed });
    try {
      const result = await api.ask(trimmed);
      setPhase({ kind: "done", result });
    } catch (error) {
      setPhase({ kind: "error", error, question: trimmed });
    }
  };

  return (
    <div className="reading-column">
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        向你们的安环资料提问
      </Typography.Title>
      <Input.TextArea
        rows={3}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="例如：我们的废气治理采用什么方案？"
        disabled={phase.kind === "loading"}
      />
      <div style={{ marginTop: 12, textAlign: "right" }}>
        <Button
          type="primary"
          loading={phase.kind === "loading"}
          disabled={!question.trim()}
          onClick={() => void ask(question)}
        >
          提问
        </Button>
      </div>

      <div style={{ marginTop: 32 }}>
        {phase.kind === "idle" && (
          <Typography.Paragraph type="secondary">
            回答只来自已发布的资料，并附引用出处。
          </Typography.Paragraph>
        )}

        {phase.kind === "loading" && (
          <>
            <Typography.Paragraph type="secondary">正在查证…</Typography.Paragraph>
            <Skeleton active paragraph={{ rows: 3 }} title={false} />
          </>
        )}

        {phase.kind === "error" && (
          <ErrorState error={phase.error} onRetry={() => void ask(phase.question)} />
        )}

        {phase.kind === "done" && phase.result.inProgress && (
          <div>
            <Typography.Paragraph type="secondary">
              问题正在处理中，请稍后重试。
            </Typography.Paragraph>
            <Button onClick={() => void ask(question)}>重试</Button>
          </div>
        )}

        {phase.kind === "done" && !phase.result.inProgress && phase.result.refusal && (
          <Typography.Paragraph type="secondary">
            现有资料无法回答这个问题。你可以换一种问法，或联系服务商补充相关资料。
          </Typography.Paragraph>
        )}

        {phase.kind === "done" &&
          !phase.result.inProgress &&
          !phase.result.refusal &&
          phase.result.answer && (
            <div className="eco-fade-up">
              <div style={{ borderTop: "1px solid var(--eco-border)", paddingTop: 24 }}>
                <div className="doc-body">
                  <Typography.Paragraph style={{ fontSize: 16, lineHeight: 1.9 }}>
                    {phase.result.answer}
                  </Typography.Paragraph>
                </div>
                {phase.result.citations.length > 0 && (
                  <div
                    style={{
                      marginTop: 24,
                      borderTop: "1px solid var(--eco-border)",
                      paddingTop: 16,
                    }}
                  >
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      引用
                    </Typography.Text>
                    {phase.result.citations.map((c, i) => (
                      <div key={`${c.documentName}-${i}`} style={{ padding: "6px 0" }}>
                        <span className="citation-ref">[{i + 1}]</span>{" "}
                        <Typography.Text style={{ fontSize: 14 }}>
                          {c.documentName} · 第 {c.pageNumber} 页
                        </Typography.Text>
                        <div>
                          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                            {c.snippet}
                          </Typography.Text>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
      </div>
    </div>
  );
}
