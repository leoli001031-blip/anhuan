"""Quality gate accepts real parser evidence, rejects broken identity/gold and empty work."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('material_quality_gate', ROOT / 'scripts/material_quality_gate.py')
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
FIXTURES = ROOT / 'tests/material_quality_samples'


class MaterialQualityGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='material-quality-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for source in FIXTURES.iterdir():
            if source.is_file():
                shutil.copyfile(source, self.root / source.name)
        self.data = json.loads((self.root / 'manifest.json').read_text())

    def run_manifest(self, *, real=False):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(self.data))
        return gate.run_quality(manifest=path, synthetic=not real)

    def real_manifest(self):
        # This is a routing test using synthetic fixture bytes, not real quality evidence.
        self.data['scope'] = 'real'
        self.data['authorized_for_local_processing'] = True
        self.data['gold_review'] = {'reviewer': 'SYNTHETIC-TEST-ONLY', 'reviewed_at': '2026-09-09', 'method': 'mode-boundary test'}
        self.data['samples'] = [s for s in self.data['samples'] if 'expected_failure' not in s]
        for sample in self.data['samples']:
            sample.pop('synthetic_ocr', None)

    def test_actual_four_format_parsers_and_adversarial_gold_are_measured(self):
        with patch('platform_foundation.f1.features.material_intake.ocr.ocr_pdf_pages', side_effect=AssertionError('no paid OCR')), \
             patch('platform_foundation.f1.features.evidence.jpeg_native._default_transport', side_effect=AssertionError('no network')):
            result = self.run_manifest()
        self.assertEqual(result['status'], 'TARGETED_TEST_PASSED')
        self.assertEqual(result['counts'], {'total': 12, 'passed': 4, 'failed': 8, 'not_tested': 0, 'expected_rejections': 8})
        self.assertEqual({s['format'] for s in result['samples'] if s['outcome'] == 'PASS'}, gate.FORMATS)
        self.assertEqual(result['ocr_live'], 'NOT_TESTED')
        self.assertTrue(all(s['comparison']['block_results'] for s in result['samples'] if s['outcome'] == 'PASS'))
        self.assertEqual(len(result['implementation_sha256']), 64)

    def test_ambiguous_or_boolean_manifest_schema_is_rejected(self):
        path = self.root / 'manifest.json'
        path.write_text('{"scope":"real","scope":"synthetic"}')
        result = gate.run_quality(manifest=path, synthetic=True)
        self.assertEqual(result['reason'], 'MANIFEST_DUPLICATE_KEY')
        self.data['schema_version'] = True
        self.assertEqual(self.run_manifest()['reason'], 'MANIFEST_SCOPE_INVALID')

    def test_real_missing_input_never_runs_synthetic(self):
        result = gate.run_quality()
        self.assertEqual((result['status'], result['reason'], result['counts']['total']), ('NOT_TESTED', 'REAL_MATERIAL_MANIFEST_REQUIRED', 0))

    def test_real_mode_does_not_replace_jpeg_ocr_with_environment_provider(self):
        self.real_manifest()
        with patch('platform_foundation.f1.features.evidence.jpeg_native._default_transport', side_effect=AssertionError('network forbidden')):
            result = self.run_manifest(real=True)
        self.assertEqual(result['status'], 'NOT_TESTED')
        self.assertEqual(result['counts']['passed'], 3)
        self.assertEqual(result['counts']['not_tested'], 1)
        self.assertEqual(result['samples'][-1]['reason'], 'REAL_OCR_INPUT_UNAVAILABLE')

    def test_scanned_pdf_never_falls_back_to_ambient_ocr(self):
        from pypdf import PdfWriter
        import io
        writer = PdfWriter(); writer.add_blank_page(100, 100)
        output = io.BytesIO(); writer.write(output)
        with patch('platform_foundation.f1.features.material_intake.ocr.ocr_pdf_pages', side_effect=AssertionError('network forbidden')):
            actual, metadata = gate.extract(output.getvalue(), 'pdf')
        self.assertEqual(actual, [])
        self.assertEqual(metadata['state'], 'NOT_TESTED')

    def test_changed_original_and_invalid_gold_cannot_pass(self):
        original = copy.deepcopy(self.data)
        for mutate, reason in [
            (lambda s: s.update(sha256='0' * 64), 'INPUT_FINGERPRINT_MISMATCH'),
            (lambda s: s.update(gold=[]), 'GOLD_EMPTY_OR_INVALID'),
            (lambda s: s.update(path='../outside.pdf'), 'INPUT_PATH_INVALID'),
        ]:
            with self.subTest(reason=reason):
                self.data = copy.deepcopy(original)
                mutate(self.data['samples'][0])
                result = self.run_manifest()
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(result['samples'][0]['reason'], reason)

    def test_empty_duplicate_and_incomplete_format_sets_fail(self):
        original = copy.deepcopy(self.data)
        for samples, reason in [([], 'SAMPLE_SET_EMPTY_OR_INVALID'),
            ([original['samples'][0], original['samples'][0]], 'SAMPLE_ID_OR_FORMAT_INVALID'),
            ([original['samples'][0]], 'FOUR_FORMAT_COVERAGE_REQUIRED')]:
            with self.subTest(reason=reason):
                self.data['samples'] = samples
                result = self.run_manifest()
                self.assertEqual((result['status'], result['reason']), ('FAIL', reason))

    def test_real_scope_requires_review_and_rejects_stub_and_expected_failures(self):
        self.real_manifest()
        original = copy.deepcopy(self.data)
        for mutation, reason in [
            (lambda d: d.pop('gold_review'), 'REAL_GOLD_REVIEW_AND_AUTHORIZATION_REQUIRED'),
            (lambda d: d['samples'][0].update(expected_failure='GOLD_MISMATCH'), 'SYNTHETIC_CONTROL_FORBIDDEN_IN_REAL_MODE'),
            (lambda d: d['samples'][-1].update(synthetic_ocr={}), 'SYNTHETIC_CONTROL_FORBIDDEN_IN_REAL_MODE'),
        ]:
            with self.subTest(reason=reason):
                self.data = copy.deepcopy(original); mutation(self.data)
                result = self.run_manifest(real=True)
                self.assertEqual((result['status'], result['reason']), ('FAILED', reason))

    def test_wrong_number_unit_and_location_fail_independently(self):
        original = copy.deepcopy(self.data)
        for name in ('numbers_exact', 'units_exact', 'locations_exact', 'no_omissions'):
            with self.subTest(check=name):
                self.data = copy.deepcopy(original)
                sample = next(s for s in self.data['samples'] if s.get('expected_failure') == name)
                sample.pop('expected_failure')
                result = self.run_manifest()
                record = next(s for s in result['samples'] if s['id'] == sample['id'])
                self.assertEqual(result['status'], 'FAIL')
                self.assertFalse(record['comparison']['checks'][name])

    def test_numbers_swapped_across_pages_do_not_pass_numeric_check(self):
        gold = copy.deepcopy(self.data['samples'][0]['gold'])
        actual = copy.deepcopy(gold)
        actual[0]['text'] = actual[0]['text'].replace('42.50', '38.25')
        actual[1]['text'] = actual[1]['text'].replace('38.25', '42.50')
        result = gate.compare(gold, actual)
        self.assertEqual(result['expected_number_count'], result['actual_number_count'])
        self.assertFalse(result['checks']['numbers_exact'])

    def test_rendered_image_identity_mismatch_rejects_stub(self):
        self.data['samples'][6]['synthetic_ocr']['rendered_sha256'] = '0' * 64
        result = self.run_manifest()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['samples'][6]['outcome'], 'FAIL')

    def test_parser_dependency_unavailable_is_not_a_pass_or_expected_rejection(self):
        with patch.object(gate, 'extract', side_effect=ModuleNotFoundError('missing')):
            result = self.run_manifest()
        self.assertNotEqual(result['status'], 'TARGETED_TEST_PASSED')
        self.assertEqual(result['counts']['passed'], 0)
        self.assertEqual(result['counts']['expected_rejections'], 0)

    def live_config(self):
        from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
        key = self.root / 'contract-key'; key.write_text('synthetic-contract-key'); key.chmod(0o600)
        return CloudOcrConfig(provider='glm_vision', model='synthetic-contract-model',
            api_key_file=key, base_url='https://example.invalid')

    def test_live_requires_explicit_real_scope_and_review_before_transport(self):
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', side_effect=AssertionError('must not call')):
            result = gate.run_quality(synthetic=True, live_ocr=True)
            self.assertEqual(result['reason'], 'LIVE_OCR_FORBIDDEN_IN_SYNTHETIC_MODE')
            self.real_manifest(); self.data.pop('gold_review')
            path = self.root / 'manifest.json'; path.write_text(json.dumps(self.data))
            result = gate.run_quality(manifest=path, live_ocr=True)
            self.assertEqual(result['reason'], 'REAL_GOLD_REVIEW_AND_AUTHORIZATION_REQUIRED')

    def test_live_prevalidates_last_input_before_any_billable_call(self):
        self.real_manifest()
        self.data['samples'][-1]['sha256'] = '0' * 64
        path = self.root / 'manifest.json'; path.write_text(json.dumps(self.data))
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', side_effect=AssertionError('must not call')):
            result = gate.run_quality(manifest=path, live_ocr=True)
        self.assertEqual((result['status'], result['reason']), ('FAILED', 'INPUT_FINGERPRINT_MISMATCH'))
        self.assertEqual(result['ocr_live'], 'NOT_TESTED')

    def test_live_jpeg_actual_parser_uses_configured_transport_and_records_identity(self):
        self.real_manifest()
        path = self.root / 'manifest.json'; path.write_text(json.dumps(self.data))
        response = gate.canonical({'choices': [{'message': {'content': 'SYNTHETIC COD 42.50 mg/L; mass 1.25 kg'}, 'finish_reason': 'stop'}]})
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr.CloudOcrConfig.from_environment', return_value=self.live_config()), \
             patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', return_value=response) as transport, \
             patch.dict('os.environ', {'F1_MATERIAL_OCR_ENABLED': '0'}):
            result = gate.run_quality(manifest=path, live_ocr=True)
        # This verifies the live switch contract using a transport fake, not real model quality.
        self.assertEqual((result['status'], result['ocr_live']), ('PASSED', 'EXECUTED'))
        self.assertEqual(transport.call_count, 1)
        evidence = result['samples'][-1]['extraction']['live_ocr']
        self.assertEqual(evidence['model'], 'synthetic-contract-model')
        self.assertEqual(len(evidence['prompt_sha256']), 64)
        self.assertEqual(evidence['requests'][0]['state'], 'RESPONSE_RECEIVED')
        self.assertEqual(evidence['requests'][0]['rendered_sha256'], self.data['samples'][-1]['gold'][0]['locator']['rendered_sha256'])
        self.assertNotIn('synthetic-contract-key', json.dumps(result))
        self.assertEqual(result['human_acceptance'], 'NOT_TESTED')

    def test_live_scanned_pdf_runs_actual_cloud_renderer_and_text_pipeline(self):
        from pypdf import PdfWriter
        import io
        writer = PdfWriter(); writer.add_blank_page(100, 100)
        output = io.BytesIO(); writer.write(output)
        text = 'SYNTHETIC discharge inspection: COD 42.50 mg/L; mass 1.25 kg.'
        response = gate.canonical({'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]})
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr.CloudOcrConfig.from_environment', return_value=self.live_config()), \
             patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', return_value=response) as transport:
            actual, metadata = gate.extract(output.getvalue(), 'pdf', live_ocr=True)
        self.assertEqual(actual[0]['text'], text)
        self.assertEqual(actual[0]['locator']['page_number'], 1)
        self.assertEqual(metadata['live_ocr']['ocr_live'], 'EXECUTED')
        self.assertEqual(metadata['pages'][0]['ocr_applied'], True)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(len(metadata['live_ocr']['requests'][0]['rendered_sha256']), 64)

    def test_live_missing_configuration_remains_not_tested(self):
        from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
        self.real_manifest()
        path = self.root / 'manifest.json'; path.write_text(json.dumps(self.data))
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr.CloudOcrConfig.from_environment', return_value=CloudOcrConfig()), \
             patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', side_effect=AssertionError('must not call')):
            result = gate.run_quality(manifest=path, live_ocr=True)
        self.assertEqual((result['status'], result['ocr_live']), ('NOT_TESTED', 'NOT_TESTED'))
        self.assertEqual(result['samples'][-1]['reason'], 'LIVE_OCR_UNAVAILABLE')

    def test_live_transport_failure_and_empty_response_are_failed_not_unmeasured(self):
        from urllib.error import URLError
        self.real_manifest()
        path = self.root / 'manifest.json'; path.write_text(json.dumps(self.data))
        for response in (URLError('synthetic-offline-failure'), b'{}', b''):
            with self.subTest(response=type(response).__name__):
                kwargs = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch('platform_foundation.f1.features.material_intake.cloud_ocr.CloudOcrConfig.from_environment', return_value=self.live_config()), \
                     patch('platform_foundation.f1.features.material_intake.cloud_ocr._default_transport', **kwargs), \
                     patch.dict('os.environ', {'F1_MATERIAL_OCR_ENABLED': '0'}):
                    result = gate.run_quality(manifest=path, live_ocr=True)
                self.assertEqual((result['status'], result['ocr_live']), ('FAILED', 'FAILED'))
                self.assertEqual(result['samples'][-1]['reason'], 'LIVE_OCR_FAILED')

    def test_cli_exit_codes_and_private_evidence(self):
        for extra, expected, state in [([], 2, 'NOT_TESTED'), (['--synthetic'], 0, 'TARGETED_TEST_PASSED')]:
            output = self.root / ('output-' + state + '.json')
            proc = subprocess.run([sys.executable, str(ROOT / 'scripts/material_quality_gate.py'), *extra, '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, expected, proc.stderr)
            evidence = json.loads(output.read_text())
            self.assertEqual(evidence['status'], state)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
