"""Pure offline contracts for the opt-in GLM report generator."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from platform_foundation.f1.features.analysis_reports.contracts import (
    SECTION_KEYS,
    EligibleSource,
    EvidenceUnit,
    FrozenSourceSet,
    GeneratedReport,
    GenerationFailed,
)
from platform_foundation.f1.features.analysis_reports.llm_generator import (
    LlmReportGenerator,
    llm_generation_enabled,
)


_PROVIDER_TEXT = (
    "排污许可证年度执行报告中记录了废水总排放口监测结果，"
    "化学需氧量浓度为每升四十二毫克，符合许可限值要求。"
)
_CLIENT_TEXT = (
    "危险废物暂存间标识牌缺失，台账中部分转移联单未归档，"
    "上次环保检查要求限期整改完成。"
)
_LONG_BODY = "整改建议正文。" * 20


def _unit(page: int, ordinal: int, text: str) -> EvidenceUnit:
    return EvidenceUnit(
        page_number=page,
        ordinal=ordinal,
        body_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


def _frozen() -> FrozenSourceSet:
    provider = EligibleSource(
        document_version_id=uuid.uuid4(),
        document_name="排污许可证执行报告",
        version_number=1,
        source_sha256="a" * 64,
        scope_kind="service_provider",
        page_number=1,
        evidence_units=(_unit(1, 1, _PROVIDER_TEXT), _unit(2, 1, _PROVIDER_TEXT)),
    )
    client = EligibleSource(
        document_version_id=uuid.uuid4(),
        document_name="环保检查整改台账",
        version_number=2,
        source_sha256="b" * 64,
        scope_kind="client",
        page_number=1,
        evidence_units=(_unit(1, 1, _CLIENT_TEXT), _unit(3, 1, _CLIENT_TEXT)),
    )
    return FrozenSourceSet(
        enterprise_id=uuid.uuid4(),
        client_account_id=uuid.uuid4(),
        template_id="enterprise-ehs-material-analysis-v1",
        fingerprint_sha256="c" * 64,
        sources=(provider, client),
    )


def _model_payload(indices: list[int], body: str = _LONG_BODY) -> bytes:
    return json.dumps(
        {
            "source_scope": body,
            "status_summary": body,
            "key_findings": body,
            "risks_and_gaps": body,
            "remediation": body,
            "citations": indices,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class _Env:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="llm-report-test-"))
        self.directory.chmod(0o700)
        self.key_file = self.directory / "api_key"
        self.key_file.write_text("test-llm-key-value", encoding="ascii")
        self.key_file.chmod(0o600)
        self._names = (
            "F1_MATERIAL_ANALYSIS_REPORT_LLM",
            "F1_MATERIAL_ANALYSIS_REPORT_LLM_MODEL",
            "F1_MATERIAL_CLOUD_OCR_PROVIDER",
            "F1_MATERIAL_CLOUD_OCR_API_KEY_FILE",
            "F1_MATERIAL_CLOUD_OCR_MODEL",
            "F1_MATERIAL_CLOUD_OCR_DIALECT",
            "F1_MATERIAL_CLOUD_OCR_BASE_URL",
            "F1_MATERIAL_OCR_ENABLED",
        )
        self._original = {name: os.environ.get(name) for name in self._names}
        for name in self._names:
            os.environ.pop(name, None)
        testcase.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, value in self._original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def enable(self, *, dialect: str = "chat", model: str = "glm-5.3-flash") -> None:
        os.environ["F1_MATERIAL_ANALYSIS_REPORT_LLM"] = "1"
        os.environ["F1_MATERIAL_ANALYSIS_REPORT_LLM_MODEL"] = model
        os.environ["F1_MATERIAL_CLOUD_OCR_PROVIDER"] = "glm_vision"
        os.environ["F1_MATERIAL_CLOUD_OCR_API_KEY_FILE"] = str(self.key_file)
        os.environ["F1_MATERIAL_CLOUD_OCR_MODEL"] = model
        os.environ["F1_MATERIAL_CLOUD_OCR_DIALECT"] = dialect
        os.environ["F1_MATERIAL_OCR_ENABLED"] = "0"


def _chat_envelope(payload: bytes) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": payload.decode("utf-8")}}]}
    ).encode("utf-8")


def _capture_transport(response: bytes):
    calls: list[tuple[str, dict[str, str], bytes, float]] = []

    def transport(url, headers, body, timeout):
        calls.append((url, dict(headers), bytes(body), timeout))
        return response

    return transport, calls


class LlmReportGeneratorContracts(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        _Env(self)
        self.assertFalse(llm_generation_enabled())

    def test_missing_cloud_configuration_fails_closed(self) -> None:
        _Env(self)
        with self.assertRaises(GenerationFailed) as raised:
            LlmReportGenerator().generate(_frozen())
        self.assertEqual(raised.exception.reason, "REPORT_LLM_CONFIGURATION_INVALID")

    def test_happy_path_maps_whitelisted_citations(self) -> None:
        env = _Env(self)
        env.enable()
        frozen = _frozen()
        transport, calls = _capture_transport(_chat_envelope(_model_payload([1, 4])))
        report = LlmReportGenerator(transport=transport).generate(frozen)
        self.assertIsInstance(report, GeneratedReport)
        self.assertEqual(
            tuple(section.key for section in report.sections), SECTION_KEYS
        )
        sections = {section.key: section for section in report.sections}
        self.assertEqual(sections["usage_boundary"].title, "使用边界")
        self.assertIn("大语言模型", sections["usage_boundary"].body)
        self.assertIn("证据白名单", sections["usage_boundary"].body)
        self.assertIn("整改", sections["remediation"].body)
        self.assertEqual(len(report.citations), 2)
        provider_source, client_source = frozen.sources
        self.assertEqual(
            report.citations[0].document_version_id,
            provider_source.document_version_id,
        )
        self.assertEqual(report.citations[0].page_number, 1)
        self.assertEqual(
            report.citations[1].document_version_id,
            client_source.document_version_id,
        )
        self.assertEqual(report.citations[1].page_number, 3)
        self.assertIn("[证据2]", sections["citations"].body)
        self.assertIn("第3页", sections["citations"].body)

        self.assertEqual(len(calls), 1)
        url, headers, body, timeout = calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer test-llm-key-value")
        request = json.loads(body.decode("utf-8"))
        self.assertEqual(request["model"], "glm-5.3-flash")
        prompt = request["messages"][0]["content"]
        self.assertIn("只依据下方编号证据块", prompt)
        self.assertIn("[1] 《排污许可证执行报告》v1", prompt)
        self.assertIn("[4] 《环保检查整改台账》v2 第3页", prompt)
        self.assertGreater(timeout, 0)

    def test_anthropic_dialect_shape(self) -> None:
        env = _Env(self)
        env.enable(dialect="anthropic")
        transport, calls = _capture_transport(
            json.dumps(
                {"content": [{"type": "text", "text": _model_payload([1, 2]).decode("utf-8")}]}
            ).encode("utf-8")
        )
        report = LlmReportGenerator(transport=transport).generate(_frozen())
        self.assertEqual(len(report.citations), 2)
        url, headers, body, _ = calls[0]
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(headers.get("anthropic-version"), "2023-06-01")
        request = json.loads(body.decode("utf-8"))
        self.assertEqual(request["max_tokens"], 16384)
        self.assertIn("证据块", request["messages"][0]["content"])

    def test_citation_out_of_whitelist_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        transport, _ = _capture_transport(_chat_envelope(_model_payload([1, 99])))
        with self.assertRaises(GenerationFailed) as raised:
            LlmReportGenerator(transport=transport).generate(_frozen())
        self.assertEqual(raised.exception.reason, "REPORT_LLM_OUTPUT_INVALID")

    def test_malformed_json_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        transport, _ = _capture_transport(_chat_envelope(b"not json at all"))
        with self.assertRaises(GenerationFailed) as raised:
            LlmReportGenerator(transport=transport).generate(_frozen())
        self.assertEqual(raised.exception.reason, "REPORT_LLM_RESPONSE_INVALID")

    def test_missing_section_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        payload = json.loads(_model_payload([1, 2]).decode("utf-8"))
        del payload["remediation"]
        transport, _ = _capture_transport(
            _chat_envelope(json.dumps(payload).encode("utf-8"))
        )
        with self.assertRaises(GenerationFailed) as raised:
            LlmReportGenerator(transport=transport).generate(_frozen())
        self.assertEqual(raised.exception.reason, "REPORT_LLM_OUTPUT_INVALID")

    def test_transport_failure_fixed_reason_without_key_leak(self) -> None:
        env = _Env(self)
        env.enable()

        def failing(url, headers, body, timeout):
            raise OSError("reset")

        try:
            LlmReportGenerator(transport=failing).generate(_frozen())
        except GenerationFailed as error:
            self.assertEqual(error.reason, "REPORT_LLM_UNAVAILABLE")
            self.assertNotIn("test-llm-key-value", str(error))
        else:
            self.fail("GenerationFailed expected")

    def test_long_unit_excerpt_capped_at_db_contract(self) -> None:
        env = _Env(self)
        env.enable()
        long_text = "整改建议正文。" * 200  # 1400 chars, well above 320
        unit = _unit(2, 1, long_text)
        frozen = FrozenSourceSet(
            enterprise_id=uuid.uuid4(),
            client_account_id=uuid.uuid4(),
            template_id="enterprise-ehs-material-analysis-v1",
            fingerprint_sha256="c" * 64,
            sources=(
                EligibleSource(
                    document_version_id=uuid.uuid4(),
                    document_name="长材料",
                    version_number=1,
                    source_sha256="a" * 64,
                    scope_kind="client",
                    page_number=1,
                    evidence_units=(_unit(1, 1, _CLIENT_TEXT), unit),
                ),
            ),
        )
        transport, _ = _capture_transport(
            _chat_envelope(_model_payload([1, 2]))
        )
        report = LlmReportGenerator(transport=transport).generate(frozen)
        self.assertTrue(
            all(len(c.excerpt) <= 320 for c in report.citations)
        )
        longest = max(report.citations, key=lambda c: len(c.excerpt))
        self.assertEqual(longest.excerpt, long_text.strip()[:320])

    def test_too_few_citations_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        transport, _ = _capture_transport(_chat_envelope(_model_payload([1])))
        with self.assertRaises(GenerationFailed) as raised:
            LlmReportGenerator(transport=transport).generate(_frozen())
        self.assertEqual(raised.exception.reason, "REPORT_LLM_OUTPUT_INVALID")



class RoundRobinAllocationContracts(unittest.TestCase):
    """P1 fix: no source may be entirely omitted by the budget."""

    def _mk_unit(self, page, ordinal, tag):
        text = f"{tag}——材料内容" * 8
        return EvidenceUnit(
            page_number=page, ordinal=ordinal,
            body_sha256=hashlib.sha256(text.encode()).hexdigest(), text=text,
        )

    def _frozen(self, count_a, count_b, tag_a="材料甲", tag_b="材料乙"):
        return FrozenSourceSet(
            enterprise_id=uuid.uuid4(), client_account_id=uuid.uuid4(),
            template_id="enterprise-ehs-material-analysis-v1",
            fingerprint_sha256="c" * 64,
            sources=(
                EligibleSource(
                    document_version_id=uuid.uuid4(), document_name=tag_a,
                    version_number=1, source_sha256="a" * 64,
                    scope_kind="client", page_number=1,
                    evidence_units=tuple(self._mk_unit(i, 1, tag_a) for i in range(1, count_a + 1)),
                ),
                EligibleSource(
                    document_version_id=uuid.uuid4(), document_name=tag_b,
                    version_number=1, source_sha256="b" * 64,
                    scope_kind="client", page_number=1,
                    evidence_units=tuple(self._mk_unit(i, 1, tag_b) for i in range(1, count_b + 1)),
                ),
            ),
        )

    def test_second_source_represented_when_first_exhausts_budget(self) -> None:
        from platform_foundation.f1.features.analysis_reports.llm_generator import (
            _evidence_blocks, _MAX_EVIDENCE_BLOCKS,
        )
        frozen = self._frozen(count_a=_MAX_EVIDENCE_BLOCKS, count_b=3)
        blocks = _evidence_blocks(frozen)
        self.assertEqual(len(blocks), _MAX_EVIDENCE_BLOCKS)
        names = {b.source.document_name for b in blocks}
        self.assertIn("材料乙", names, "second source must not be omitted")
        b_count = sum(1 for b in blocks if b.source.document_name == "材料乙")
        self.assertEqual(b_count, 3)

    def test_prompt_includes_later_source_text(self) -> None:
        env = _Env(self)
        env.enable()
        frozen = self._frozen(count_a=70, count_b=3)
        from platform_foundation.f1.features.analysis_reports.llm_generator import (
            _evidence_blocks,
        )
        blocks = _evidence_blocks(frozen)
        prompt_lines = [b.unit.text[:20] for b in blocks]
        self.assertTrue(any("材料乙" in t for t in prompt_lines))



class HttpsEnforcementContracts(unittest.TestCase):
    """P2 fix: LLM report generation must refuse plaintext HTTP endpoints."""

    def test_http_base_url_rejected_before_any_key_read(self) -> None:
        env = _Env(self)
        env.enable()
        os.environ["F1_MATERIAL_CLOUD_OCR_BASE_URL"] = "http://example.invalid/api"
        # capability check itself would reject this, but _llm_settings must
        # also refuse — defense in depth before any secret leaves the process
        with self.assertRaises(GenerationFailed) as raised:
            from platform_foundation.f1.features.analysis_reports.llm_generator import (
                _llm_settings,
            )
            _llm_settings()
        self.assertEqual(
            raised.exception.reason, "REPORT_LLM_CONFIGURATION_INVALID"
        )


if __name__ == "__main__":
    unittest.main()
