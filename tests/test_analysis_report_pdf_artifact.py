"""Pure offline contracts for the deterministic PDF report artifact."""
from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from pathlib import Path

from platform_foundation.f1.features.analysis_reports.artifact import (
    ReportArtifactInvalid,
)
from platform_foundation.f1.features.analysis_reports.pdf_artifact import (
    render_pdf_artifact,
)


ROOT = Path(__file__).resolve().parents[1]


def _payload() -> dict[str, object]:
    sections = [
        {"key": key, "title": key, "body": f"{key} 正文。" + "内容" * 30}
        for key in (
            "source_scope",
            "status_summary",
            "key_findings",
            "risks_and_gaps",
            "remediation",
            "citations",
            "usage_boundary",
        )
    ]
    citations = [
        {
            "document_version_id": str(uuid.uuid4()),
            "document_name": f"材料{index}",
            "version_number": index,
            "page_number": index,
            "excerpt": f"证据摘录 {index}：" + "原文" * 20,
        }
        for index in (1, 2, 3)
    ]
    return {"version_number": 1, "sections": sections, "citations": citations}


class PdfArtifactContracts(unittest.TestCase):
    def test_renders_deterministic_pdf(self) -> None:
        first = render_pdf_artifact(_payload())
        second = render_pdf_artifact(_payload())
        self.assertTrue(first.body.startswith(b"%PDF-"))
        self.assertEqual(first.filename, "a-eco-analysis-report-v1.pdf")
        self.assertEqual(first.body, second.body)
        self.assertEqual(
            first.sha256, hashlib.sha256(first.body).hexdigest()
        )
        self.assertGreater(len(first.body), 10_000)

    def test_font_override_env_respected(self) -> None:
        original = os.environ.get("F1_REPORT_PDF_FONT")
        os.environ["F1_REPORT_PDF_FONT"] = "/nonexistent/font.otf"
        try:
            with self.assertRaises(ReportArtifactInvalid) as raised:
                render_pdf_artifact(_payload())
            self.assertEqual(
                raised.exception.args[0], "REPORT_PDF_FONT_UNAVAILABLE"
            )
        finally:
            if original is None:
                os.environ.pop("F1_REPORT_PDF_FONT", None)
            else:
                os.environ["F1_REPORT_PDF_FONT"] = original

    def test_incomplete_sections_rejected(self) -> None:
        payload = _payload()
        payload["sections"] = payload["sections"][:-1]
        with self.assertRaises(ReportArtifactInvalid) as raised:
            render_pdf_artifact(payload)
        self.assertEqual(
            raised.exception.args[0], "REPORT_ARTIFACT_SECTIONS_INCOMPLETE"
        )

    def test_missing_citations_rejected(self) -> None:
        payload = _payload()
        payload["citations"] = []
        with self.assertRaises(ReportArtifactInvalid) as raised:
            render_pdf_artifact(payload)
        self.assertEqual(
            raised.exception.args[0], "REPORT_ARTIFACT_CITATIONS_REQUIRED"
        )


if __name__ == "__main__":
    unittest.main()
