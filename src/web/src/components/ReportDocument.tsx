// 报告文档渲染：客户端详情与运营台预览共用同一版式（方向 A：排版+分隔线，无卡片）。
// 七章顺序冻结；引用证据只展示文档名、版本、页码、摘录。
import { useState } from "react";
import { Drawer, Typography } from "antd";
import type { CitationV1, SectionV1 } from "../adapters/types";
import { SECTION_ORDER } from "../adapters/types";

export function formatDateTime(iso: string): string {
  // ISO → "2026-08-21 10:00"，不做时区转换以外的任何加工
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function ReportDocument({
  sections,
  citations,
  serif = true,
}: {
  sections: SectionV1[];
  citations: CitationV1[];
  serif?: boolean;
}) {
  const byKey = new Map(sections.map((s) => [s.key, s]));
  const [open, setOpen] = useState<CitationV1 | null>(null);
  return (
    <div className="report-document">
      {SECTION_ORDER.map(({ key, title }, index) => {
        const section = byKey.get(key);
        if (!section) return null;
        return (
          <section key={key} id={`section-${key}`} className="doc-section">
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {["一", "二", "三", "四", "五", "六", "七"][index]}、{section.title || title}
            </Typography.Title>
            <div className={serif ? "doc-body" : undefined}>
              <Typography.Paragraph style={{ fontSize: serif ? 16 : 14, lineHeight: 1.9 }}>
                {section.body}
              </Typography.Paragraph>
            </div>
            {key === "citations" && citations.length > 0 && (
              <div className="citation-list">
                {citations.map((c, i) => (
                  <div key={c.citation_id} className="citation-list__item">
                    <button
                      type="button"
                      className="citation-ref"
                      style={{
                        background: "none",
                        border: "none",
                        padding: 0,
                        cursor: "pointer",
                        color: "var(--eco-primary)",
                      }}
                      onClick={() => setOpen(c)}
                    >
                      [{i + 1}]
                    </button>{" "}
                    <Typography.Text>
                      {c.documentName} · 第 {c.pageNumber} 页（第 {c.versionNumber} 版）
                    </Typography.Text>
                    <div>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        {c.excerpt}
                      </Typography.Text>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
      <Drawer
        title="引用证据"
        open={open !== null}
        onClose={() => setOpen(null)}
      >
        {open && (
          <div>
            <Typography.Paragraph>
              <Typography.Text strong>{open.documentName}</Typography.Text>
            </Typography.Paragraph>
            <Typography.Paragraph type="secondary">
              第 {open.versionNumber} 版 · 第 {open.pageNumber} 页
            </Typography.Paragraph>
            <Typography.Paragraph>{open.excerpt}</Typography.Paragraph>
          </div>
        )}
      </Drawer>
    </div>
  );
}
