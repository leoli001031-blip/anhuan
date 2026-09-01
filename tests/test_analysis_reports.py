"""Offline evidence-driven analysis-report generator contracts."""
from __future__ import annotations

import hashlib
import unittest
import uuid

from platform_foundation.f1.features.analysis_reports.contracts import (
    EligibleSource,
    EvidenceUnit,
    FrozenSourceSet,
    GenerationFailed,
    TEMPLATE_ID,
)
from platform_foundation.f1.features.analysis_reports.generator import (
    EvidenceDrivenReportGenerator,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source(
    *,
    source_id: str,
    scope_kind: str,
    document_name: str,
    text: str,
    page_number: int = 1,
) -> EligibleSource:
    return EligibleSource(
        document_version_id=uuid.UUID(source_id),
        document_name=document_name,
        version_number=1,
        source_sha256=_sha(f"source|{source_id}|{text}"),
        scope_kind=scope_kind,
        page_number=page_number,
        evidence_units=(
            EvidenceUnit(
                page_number=page_number,
                ordinal=1,
                body_sha256=_sha(text),
                text=text,
            ),
        ),
    )


def _frozen(client_text: str) -> FrozenSourceSet:
    provider_text = "服务方指引要求保存安全培训记录。"
    sources = (
        _source(
            source_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            scope_kind="service_provider",
            document_name="服务方培训指引",
            text=provider_text,
        ),
        _source(
            source_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            scope_kind="client",
            document_name="客户现场记录",
            text=client_text,
            page_number=3,
        ),
    )
    return FrozenSourceSet(
        enterprise_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        client_account_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        template_id=TEMPLATE_ID,
        fingerprint_sha256=_sha("|".join(unit.body_sha256 for source in sources for unit in source.evidence_units)),
        sources=sources,
    )


class EvidenceDrivenReportGeneratorTests(unittest.TestCase):
    def test_changed_source_text_changes_report_and_citations_are_extractive(self) -> None:
        first_client_text = "危化品台账与现场标识不一致，待整改。"
        second_client_text = "废水监测记录已按月归档。"
        generator = EvidenceDrivenReportGenerator()

        first = generator.generate(_frozen(first_client_text))
        second = generator.generate(_frozen(second_client_text))

        self.assertNotEqual(first.sections, second.sections)
        self.assertIn(first_client_text, {item.excerpt for item in first.citations})
        self.assertIn(second_client_text, {item.excerpt for item in second.citations})
        self.assertNotIn("企业应当建立安全生产责任制。", {item.excerpt for item in first.citations})
        self.assertTrue(
            all(
                citation.excerpt in {
                    "服务方指引要求保存安全培训记录。",
                    first_client_text,
                }
                for citation in first.citations
            )
        )

    def test_missing_released_unit_text_fails_closed(self) -> None:
        frozen = _frozen("客户已保存监测记录。")
        missing = EligibleSource(
            document_version_id=frozen.sources[1].document_version_id,
            document_name=frozen.sources[1].document_name,
            version_number=1,
            source_sha256=frozen.sources[1].source_sha256,
            scope_kind="client",
            page_number=1,
            evidence_units=(),
        )
        invalid = FrozenSourceSet(
            enterprise_id=frozen.enterprise_id,
            client_account_id=frozen.client_account_id,
            template_id=frozen.template_id,
            fingerprint_sha256=frozen.fingerprint_sha256,
            sources=(frozen.sources[0], missing),
        )

        with self.assertRaises(GenerationFailed) as raised:
            EvidenceDrivenReportGenerator().generate(invalid)
        self.assertEqual(raised.exception.reason, "REPORT_SOURCE_EVIDENCE_MISSING")


if __name__ == "__main__":
    unittest.main()
