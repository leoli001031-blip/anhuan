#!/usr/bin/env python3
"""Material quality gate: offline by default, explicit --live-ocr for reviewed real input.

Synthetic OCR never counts as real recognition or human acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
FORMATS = {'pdf', 'docx', 'xlsx', 'jpeg'}
MAX_INPUT = 25 * 1024 * 1024
NUMBER = re.compile(r'(?<![\w.])[+-]?\d+(?:[.,]\d+)*(?![\w.])')
UNIT = re.compile(r'(?<![A-Za-z])(?:mg/L|mg/m3|m3/h|t/a|kg|dB|m²|%)(?![A-Za-z])')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def implementation_identity():
    files = [Path(__file__), REPO / 'requirements/requirements-f1.lock',
             *sorted((REPO / 'src/platform_foundation/f1/features/evidence').glob('*.py')),
             *sorted((REPO / 'src/platform_foundation/f1/features/material_intake').glob('*.py'))]
    return sha(canonical({str(p.relative_to(REPO)): sha(p.read_bytes()) for p in files}))


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidInput('MANIFEST_DUPLICATE_KEY')
        value[key] = item
    return value


def normalized(text):
    # Preserve signs, decimals, case, punctuation and units. Only layout whitespace folds.
    return ' '.join(text.split())


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class InvalidInput(ValueError):
    pass


def read_bound(root, relative, expected, maximum=MAX_INPUT):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise InvalidInput('INPUT_PATH_INVALID')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise InvalidInput('INPUT_PATH_INVALID')
    if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
        raise InvalidInput('INPUT_SHA256_INVALID')
    if not 0 < path.stat().st_size <= maximum:
        raise InvalidInput('INPUT_SIZE_INVALID')
    body = path.read_bytes()
    if sha(body) != expected:
        raise InvalidInput('INPUT_FINGERPRINT_MISMATCH')
    return body


def validate_gold(sample):
    from platform_foundation.f1.features.evidence.contracts import parse_locator
    gold = sample.get('gold')
    if not isinstance(gold, list) or not 1 <= len(gold) <= 20000:
        raise InvalidInput('GOLD_EMPTY_OR_INVALID')
    seen = set()
    for block in gold:
        if not isinstance(block, dict) or set(block) != {'text', 'locator'}:
            raise InvalidInput('GOLD_BLOCK_INVALID')
        if not isinstance(block['text'], str) or not normalized(block['text']) or len(block['text']) > 100000:
            raise InvalidInput('GOLD_TEXT_INVALID')
        locator = parse_locator(block['locator'])
        if locator.source_format != sample['format'] or locator.sha256 in seen:
            raise InvalidInput('GOLD_LOCATION_INVALID')
        seen.add(locator.sha256)
    text = '\n'.join(item['text'] for item in gold)
    if not NUMBER.search(text) or not UNIT.search(text):
        raise InvalidInput('GOLD_NUMERIC_UNIT_COVERAGE_REQUIRED')
    return gold


def compare(gold, actual):
    expected_locations = [item['locator'] for item in gold]
    actual_locations = [item['locator'] for item in actual]
    expected_text = '\n'.join(normalized(item['text']) for item in gold)
    actual_text = '\n'.join(normalized(item['text']) for item in actual)
    expected_numbers, actual_numbers = Counter(NUMBER.findall(expected_text)), Counter(NUMBER.findall(actual_text))
    expected_units, actual_units = Counter(UNIT.findall(expected_text)), Counter(UNIT.findall(actual_text))
    expected_tokens, actual_tokens = Counter(expected_text.split()), Counter(actual_text.split())
    blocks = []
    for index, (wanted, found) in enumerate(zip_longest(gold, actual), 1):
        wt = normalized(wanted['text']) if wanted else ''
        ft = normalized(found['text']) if found else ''
        blocks.append({'ordinal': index, 'expected_locator': wanted['locator'] if wanted else None,
            'actual_locator': found['locator'] if found else None,
            'text_exact': wt == ft, 'numbers_exact': Counter(NUMBER.findall(wt)) == Counter(NUMBER.findall(ft)),
            'units_exact': Counter(UNIT.findall(wt)) == Counter(UNIT.findall(ft)),
            'expected_text_sha256': sha(wt.encode()), 'actual_text_sha256': sha(ft.encode())})
    checks = {
        'numbers_exact': expected_numbers == actual_numbers and all(b['numbers_exact'] for b in blocks),
        'units_exact': expected_units == actual_units and all(b['units_exact'] for b in blocks),
        'text_exact': [normalized(b['text']) for b in gold] == [normalized(b['text']) for b in actual],
        'locations_exact': expected_locations == actual_locations,
        'no_omissions': not (expected_tokens - actual_tokens),
        'no_additions': not (actual_tokens - expected_tokens),
    }
    return {'checks': checks, 'block_results': blocks, 'expected_blocks': len(gold), 'actual_blocks': len(actual),
            'expected_number_count': sum(expected_numbers.values()), 'actual_number_count': sum(actual_numbers.values()),
            'expected_unit_count': sum(expected_units.values()), 'actual_unit_count': sum(actual_units.values()),
            'omitted_token_count': sum((expected_tokens - actual_tokens).values()),
            'added_token_count': sum((actual_tokens - expected_tokens).values()),
            'expected_text_sha256': sha(expected_text.encode()), 'actual_text_sha256': sha(actual_text.encode())}


class LiveOcrSession:
    """Observe the actual production transport; never accept an input-file response."""
    def __init__(self):
        from platform_foundation.f1.features.material_intake import cloud_ocr, pdf_renderer
        self.module = cloud_ocr
        self.config = cloud_ocr.CloudOcrConfig.from_environment()
        self.capability = cloud_ocr.cloud_ocr_capability(self.config)
        self.evidence = {'provider': self.config.provider, 'model': self.config.model,
            'dialect': self.config.dialect, 'prompt_sha256': sha(cloud_ocr._CLOUD_OCR_PROMPT.encode()),
            'endpoint_sha256': sha(self.config.base_url.encode()), 'requests': [],
            'renderer_implementation_sha256': sha(Path(pdf_renderer.__file__).read_bytes()),
            'capability': self.capability.state, 'ocr_live': 'NOT_TESTED'}

    def transport(self, url, headers, payload, timeout):
        import base64
        request = json.loads(payload)
        part = request['messages'][0]['content'][0]
        encoded = part['source']['data'] if self.config.dialect == 'anthropic' else part['image_url']['url'].split(',', 1)[1]
        image = base64.b64decode(encoded, validate=True)
        record = {'request_sha256': sha(payload), 'rendered_sha256': sha(image),
                  'state': 'ATTEMPTED'}
        self.evidence['requests'].append(record)
        self.evidence['ocr_live'] = 'FAILED'
        raw = self.module._default_transport(url, headers, payload, timeout)
        record.update(state='RESPONSE_RECEIVED', response_sha256=sha(raw))
        return raw

    def finish(self, successful):
        if self.evidence['requests']:
            self.evidence['ocr_live'] = 'EXECUTED' if successful else 'FAILED'
        return self.evidence


def extract(source, source_format, *, synthetic_ocr=None, live_ocr=False):
    from platform_foundation.f1.features.evidence.docx_native import extract_docx
    from platform_foundation.f1.features.evidence.xlsx_native import extract_xlsx
    from platform_foundation.f1.features.evidence.jpeg_native import extract_jpeg
    from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
    from platform_foundation.f1.features.material_intake.ocr import extract_pdf_text_pages
    from platform_foundation.f1.features.evidence.contracts import PdfPageLocator
    source_hash = sha(source)
    session = None
    if source_format == 'pdf':
        def pdf_ocr(body, *, page_numbers, expected_sha256, **_kwargs):
            nonlocal session
            if not live_ocr:
                return ()
            from platform_foundation.f1.features.material_intake.cloud_ocr import cloud_ocr_pdf_pages
            session = LiveOcrSession()
            # Quality runs must observe fresh requests, never an ambient task cache.
            with patch.dict(os.environ, {'F1_OCR_RESULT_CACHE': '0'}):
                return cloud_ocr_pdf_pages(body, page_numbers=page_numbers, expected_sha256=expected_sha256,
                    config=session.config, transport=session.transport)
        pages = extract_pdf_text_pages(source, expected_sha256=source_hash, ocr_pages=pdf_ocr)
        complete = all(not p.ocr_required and p.text_source != 'none' for p in pages)
        metadata = {'parser': 'pdf-effective-text', 'text_sources': [p.text_source for p in pages],
            'ocr': 'CLOUD_LIVE' if session else ('NOT_REQUIRED' if complete else 'NOT_TESTED'),
            'pages': [{'page_number': p.page_number, 'ocr_applied': p.ocr_applied,
                'ocr_status': p.ocr_status, 'reason_codes': list(p.reason_codes)} for p in pages]}
        if session:
            metadata['live_ocr'] = session.finish(complete)
        if not complete:
            attempted = bool(session and session.evidence['requests'])
            return [], {**metadata, 'state': 'FAIL' if attempted else 'NOT_TESTED',
                'reason': 'LIVE_OCR_FAILED' if attempted else ('LIVE_OCR_UNAVAILABLE' if live_ocr else 'REAL_OCR_INPUT_UNAVAILABLE')}
        return [{'text': p.text, 'locator': PdfPageLocator(p.page_number).to_dict()} for p in pages], {**metadata, 'state': 'EXTRACTED'}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'F1_OCR_RESULT_CACHE': '0'}))
        if source_format == 'jpeg':
            # Explicit disabled config prevents reading ambient cloud key/config.
            config, transport = CloudOcrConfig(), lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
            if live_ocr:
                session = LiveOcrSession()
                config, transport = session.config, session.transport
            if synthetic_ocr is not None:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='quality-ocr-')))
                key = directory / 'synthetic-key'
                key.write_text('synthetic-offline-key'); key.chmod(0o600)
                config = CloudOcrConfig(provider='glm_vision', api_key_file=key, model='synthetic-quality-stub', base_url='https://example.invalid')
                stack.enter_context(patch.dict(os.environ, {'F1_MATERIAL_OCR_ENABLED': '0'}))
                def transport(_url, _headers, payload, _timeout):
                    import base64
                    request = json.loads(payload)
                    rendered = base64.b64decode(request['messages'][0]['content'][0]['image_url']['url'].split(',', 1)[1])
                    if sha(rendered) != synthetic_ocr['rendered_sha256']:
                        raise InvalidInput('OCR_RENDER_FINGERPRINT_MISMATCH')
                    return canonical({'choices': [{'message': {'content': synthetic_ocr['text']}, 'finish_reason': 'stop'}]})
            result = extract_jpeg(source, expected_sha256=source_hash, config=config, transport=transport)
        else:
            result = {'docx': extract_docx, 'xlsx': extract_xlsx}[source_format](source, expected_sha256=source_hash)
    metadata = {'parser': result.parser_version, 'support_profile': result.support_profile,
                'coverage': result.coverage_state, 'debts': [d.reason_code for d in result.debts],
                'processed_blocks': result.processed_block_count, 'expected_blocks': result.expected_block_count,
                'ocr': 'SYNTHETIC_STUB' if synthetic_ocr is not None else ('CLOUD_LIVE' if session else ('NOT_TESTED' if source_format == 'jpeg' else 'NOT_REQUIRED'))}
    if source_format == 'jpeg':
        metadata['processing_identity'] = result.processing_identity
    if session:
        metadata['live_ocr'] = session.finish(result.report_source_eligible)
    if source_format == 'jpeg' and synthetic_ocr is None and not result.report_source_eligible:
        attempted = bool(session and session.evidence['requests'])
        return [], {**metadata, 'state': 'FAIL' if attempted else 'NOT_TESTED',
            'reason': 'LIVE_OCR_FAILED' if attempted else ('LIVE_OCR_UNAVAILABLE' if live_ocr else 'REAL_OCR_INPUT_UNAVAILABLE')}
    if not result.report_source_eligible or result.processed_block_count != result.expected_block_count:
        return [], {**metadata, 'state': 'FAIL', 'reason': 'EXTRACTION_INCOMPLETE'}
    return [{'text': b.text, 'locator': b.locator.to_dict()} for b in result.blocks], {**metadata, 'state': 'EXTRACTED'}


def evaluate_sample(sample, root, *, scope, live_ocr=False):
    record = {'id': sample.get('id'), 'format': sample.get('format'), 'input_sha256': sample.get('sha256'),
              'gold_sha256': sha(canonical(sample.get('gold'))), 'outcome': 'FAIL'}
    try:
        gold = validate_gold(sample)
        source = read_bound(root, sample.get('path'), sample.get('sha256'))
        stub = sample.get('synthetic_ocr')
        if stub is not None and scope != 'synthetic':
            raise InvalidInput('SYNTHETIC_OCR_FORBIDDEN_IN_REAL_MODE')
        actual, extraction = extract(source, sample['format'], synthetic_ocr=stub, live_ocr=live_ocr)
        record['extraction'] = extraction
        if extraction['state'] != 'EXTRACTED':
            record.update(outcome=extraction['state'], reason=extraction['reason'])
        else:
            record['comparison'] = compare(gold, actual)
            record['outcome'] = 'PASS' if all(record['comparison']['checks'].values()) else 'FAIL'
            record['reason'] = 'EXACT_GOLD_MATCH' if record['outcome'] == 'PASS' else 'GOLD_MISMATCH'
    except (ImportError, ModuleNotFoundError) as exc:
        record.update(outcome='NOT_TESTED', reason='PARSER_DEPENDENCY_UNAVAILABLE', error_type=type(exc).__name__)
    except Exception as exc:
        # No customer text, keys or traceback in public result. Errors still fail closed.
        record['reason'] = str(exc) if isinstance(exc, InvalidInput) else 'PARSER_OR_INPUT_ERROR'
        record['error_type'] = type(exc).__name__
    return record


def run_quality(*, manifest: Path | None = None, synthetic=False, live_ocr=False):
    scope = 'synthetic' if synthetic else 'real'
    result = {'schema_version': 1, 'scope': scope, 'status': 'NOT_TESTED', 'ocr_live': 'NOT_TESTED', 'live_ocr_requested': live_ocr,
              'human_acceptance': 'NOT_TESTED', 'samples': [], 'manifest_sha256': None,
              'implementation_sha256': implementation_identity(), 'proof_state': 'NOT_TESTED', 'counts': {'total': 0, 'passed': 0, 'failed': 0, 'not_tested': 0, 'expected_rejections': 0},
              'threshold': 'all gold blocks, text, numeric tokens, units and ordered format locations exact; all four formats required',
              'limitations': ['No model/report/QA quality or business acceptance claim.', 'Live OCR requires an explicit switch and reviewed real inputs; synthetic OCR is a transport stub.']}
    if synthetic and live_ocr:
        return {**result, 'status': 'FAIL', 'proof_state': 'FAIL', 'reason': 'LIVE_OCR_FORBIDDEN_IN_SYNTHETIC_MODE'}
    if synthetic and manifest is None:
        manifest = REPO / 'tests/material_quality_samples/manifest.json'
    if manifest is None:
        return {**result, 'reason': 'REAL_MATERIAL_MANIFEST_REQUIRED'}
    try:
        manifest = Path(manifest).resolve()
        if not 0 < manifest.stat().st_size <= 4 * 1024 * 1024:
            raise InvalidInput('MANIFEST_SIZE_INVALID')
        raw = manifest.read_bytes()
        result['manifest_sha256'] = sha(raw)
        data = json.loads(raw, object_pairs_hook=strict_object)
        if type(data.get('schema_version')) is not int or data.get('schema_version') != 1 or data.get('scope') != scope:
            raise InvalidInput('MANIFEST_SCOPE_INVALID')
        if scope == 'real' and (data.get('authorized_for_local_processing') is not True or
                not isinstance(data.get('gold_review'), dict) or
                not all(isinstance(data['gold_review'].get(k), str) and data['gold_review'][k].strip() for k in ('reviewer', 'reviewed_at', 'method'))):
            raise InvalidInput('REAL_GOLD_REVIEW_AND_AUTHORIZATION_REQUIRED')
        samples = data.get('samples')
        if not isinstance(samples, list) or not 1 <= len(samples) <= 100:
            raise InvalidInput('SAMPLE_SET_EMPTY_OR_INVALID')
        ids = set()
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get('id'), str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', sample['id']) or sample['id'] in ids or sample.get('format') not in FORMATS:
                raise InvalidInput('SAMPLE_ID_OR_FORMAT_INVALID')
            ids.add(sample['id'])
            if scope == 'real' and ('expected_failure' in sample or 'synthetic_ocr' in sample):
                raise InvalidInput('SYNTHETIC_CONTROL_FORBIDDEN_IN_REAL_MODE')
        if {s['format'] for s in samples if 'expected_failure' not in s} != FORMATS:
            raise InvalidInput('FOUR_FORMAT_COVERAGE_REQUIRED')
        if live_ocr:
            # Validate the entire input set before any billable transport can run.
            for sample in samples:
                validate_gold(sample)
                read_bound(manifest.parent, sample.get('path'), sample.get('sha256'))
        for sample in samples:
            record = evaluate_sample(sample, manifest.parent, scope=scope, live_ocr=live_ocr)
            expected_failure = sample.get('expected_failure')
            if expected_failure is not None:
                record['expected_failure'] = expected_failure
                checks = record.get('comparison', {}).get('checks', {})
                record['expected_rejection_observed'] = record['outcome'] == 'FAIL' and (
                    record.get('reason') == expected_failure or checks.get(expected_failure) is False)
            result['samples'].append(record)
        live_states = [s.get('extraction', {}).get('live_ocr', {}).get('ocr_live', 'NOT_TESTED') for s in result['samples']]
        result['ocr_live'] = 'FAILED' if 'FAILED' in live_states else ('EXECUTED' if 'EXECUTED' in live_states else 'NOT_TESTED')
        result['counts'] = {'total': len(samples), 'passed': sum(s['outcome'] == 'PASS' for s in result['samples']),
            'failed': sum(s['outcome'] == 'FAIL' for s in result['samples']),
            'not_tested': sum(s['outcome'] == 'NOT_TESTED' for s in result['samples']),
            'expected_rejections': sum(s.get('expected_rejection_observed', False) for s in result['samples'])}
        failed = any(not s.get('expected_rejection_observed', s['outcome'] in ('PASS', 'NOT_TESTED')) for s in result['samples'])
        result['status'] = 'FAIL' if failed else ('NOT_TESTED' if result['counts']['not_tested'] else ('TARGETED_TEST_PASSED' if synthetic else 'REAL_EXTRACTION_QUALITY_PASSED'))
        result['reason'] = 'SYNTHETIC_ENGINEERING_ONLY' if synthetic and result['status'] == 'TARGETED_TEST_PASSED' else ('OCR_INPUT_UNAVAILABLE' if result['status'] == 'NOT_TESTED' else 'GOLD_EVALUATED')
    except Exception as exc:
        result.update(status='FAIL', reason=str(exc) if isinstance(exc, InvalidInput) else 'MANIFEST_INVALID', error_type=type(exc).__name__)
    result['proof_state'] = result['status']
    if not synthetic:
        result['status'] = {'FAIL': 'FAILED', 'REAL_EXTRACTION_QUALITY_PASSED': 'PASSED'}.get(result['status'], result['status'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic', action='store_true', help='Offline engineering regression only; never real OCR accuracy.')
    parser.add_argument('--manifest', type=Path, help='Explicit reviewed real material manifest; synthetic requires --synthetic.')
    parser.add_argument('--live-ocr', action='store_true', help='Explicitly send reviewed real OCR inputs to the configured cloud provider; may incur charges.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_quality(manifest=args.manifest, synthetic=args.synthetic, live_ocr=args.live_ocr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Artifact may contain source/gold fingerprints; keep it private by default.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'status': result['status'], 'scope': result['scope'], 'counts': result['counts'], 'output': str(args.output)}))
    return 0 if result['status'] in ('TARGETED_TEST_PASSED', 'PASSED') else (2 if result['status'] == 'NOT_TESTED' else 1)


if __name__ == '__main__':
    raise SystemExit(main())
