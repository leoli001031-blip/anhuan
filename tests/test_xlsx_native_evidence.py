"""Independent XLSX cell expectations and adversarial package mutations."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
from pathlib import Path
import unittest
import warnings
from xml.etree import ElementTree as ET
import zipfile

from platform_foundation.f1.features.evidence.contracts import XlsxCellsLocator
from platform_foundation.f1.features.evidence.ooxml_package import CT, O, R, PackageLimits
from platform_foundation.f1.features.evidence.xlsx_native import XlsxExtractionError, XlsxLimits, extract_xlsx
from platform_foundation.f1.features.evidence.xlsx_styles import S, q, display_number

ORIGINAL = Path(__file__).parent / 'fixtures/xlsx/ordinary-generated.xlsx'


def extract(data, **kwargs):
    return extract_xlsx(data, expected_sha256=hashlib.sha256(data).hexdigest(), **kwargs)


def rewrite(changes=None, edit=None, duplicates=()):
    with zipfile.ZipFile(ORIGINAL) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    for name, mutate in (changes or {}).items():
        root = ET.fromstring(parts[name])
        mutate(root)
        parts[name] = ET.tostring(root, encoding='utf-8')
    if edit:
        edit(parts)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in [*parts.items(), *duplicates]:
            archive.writestr(name, raw)
    return output.getvalue()


def cell(root, address):
    return next(node for node in root.iter(q('c')) if node.get('r') == address)


class XlsxNativeEvidence(unittest.TestCase):
    def partial(self, data, reason=None):
        result = extract(data)
        self.assertEqual(result.coverage_state, 'partial')
        self.assertFalse(result.report_source_eligible)
        self.assertEqual(result.blocks, ())
        if reason:
            self.assertIn(reason, {debt.reason_code for debt in result.debts})
        return result

    def test_ordinary_original_exact_values_sheet_identity_and_merged_range(self):
        raw = ORIGINAL.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'f7d06cede7d5d4ab51033bdea0bb003e33107335f5052b7ec8d8b64361f165d1')
        result = extract(raw)
        self.assertEqual(result.coverage_state, 'complete')
        self.assertTrue(result.report_source_eligible)
        self.assertEqual((result.expected_block_count, result.processed_block_count), (15, 15))
        self.assertEqual([block.text for block in result.blocks], ['编号', 'COD (mg/L)', '比例', '日期', '是否合格', 'A-001', '42.50', '12.5%', '2026-09-08', 'TRUE', '合并说明', '编号', '质量 (kg)', 'HW-08', '1.25'])
        self.assertEqual(result.blocks[10].locator, XlsxCellsLocator(1, '排口台账', 'xl/worksheets/sheet1.xml', 'A4:C4'))
        self.assertEqual(result.blocks[-1].locator, XlsxCellsLocator(2, '危废台账', 'xl/worksheets/sheet2.xml', 'B2'))
        self.assertNotIn('mg/L', repr(result))

    def test_nonconsecutive_sheet_ids_and_workbook_order_not_part_number(self):
        def mutate(root):
            sheets = root.find(q('sheets'))
            first, second = list(sheets)
            sheets.remove(first)
            sheets.append(first)
            second.set('sheetId', '19')
        result = extract(rewrite({'xl/workbook.xml': mutate}))
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(result.blocks[0].locator.sheet_id, 19)
        self.assertEqual(result.blocks[0].locator.sheet_name, '危废台账')
        self.assertEqual(result.blocks[0].locator.part, 'xl/worksheets/sheet2.xml')

    def test_formula_with_cache_without_cache_shared_and_string_cache_all_partial(self):
        for attrs in ({}, {'t': 'shared', 'si': '0'}, {'t': 'array', 'ref': 'B2:C2'}):
            def mutate(root):
                target = cell(root, 'B2')
                ET.SubElement(target, q('f'), attrs).text = 'SUM(A2:A5)'
            result = self.partial(rewrite({'xl/worksheets/sheet1.xml': mutate}), 'XLSX_FORMULA_UNRESOLVED')
            self.assertEqual((result.expected_block_count, result.processed_block_count), (15, 15))
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: cell(root, 'B2').set('t', 'str')}), 'XLSX_FORMULA_UNRESOLVED')

    def test_date_systems_and_fictitious_leap_day_are_distinct(self):
        for epoch, serial in (('0', '40729'), ('1', '39267')):
            result = extract(rewrite({'xl/workbook.xml': lambda root: root.find(q('workbookPr')).set('date1904', epoch),
                'xl/worksheets/sheet1.xml': lambda root: setattr(cell(root, 'D2').find(q('v')), 'text', serial)}))
            self.assertTrue(result.report_source_eligible)
            self.assertEqual(result.blocks[8].text, '2011-07-05')
        for serial in ('60', '60.5', '-1', '0'):
            self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: setattr(cell(root, 'D2').find(q('v')), 'text', serial)}), 'XLSX_NUMBER_FORMAT_UNRESOLVED')
        self.partial(rewrite({'xl/workbook.xml': lambda root: root.find(q('workbookPr')).set('date1904', 'maybe')}), 'XLSX_DATE_SYSTEM_UNRESOLVED')

    def test_bounded_numbers_percent_units_padding_dates_and_rounding(self):
        for raw, fmt, expected in [('42.5', '0.00', '42.50'), ('0.125', '0.0%', '12.5%'),
            ('12', '00000', '00012'), ('-12', '00000', '-00012'), ('12345.5', '#,##0.00', '12,345.50'),
            ('0.00340', 'General', '0.0034'), ('1.255', '0.00', '1.26'), ('42.5', '0.0" mg/L"', '42.5 mg/L'),
            ('0.5', 'h:mm', '12:00:00'), ('40729.5', 'yyyy-mm-dd hh:mm:ss', '2011-07-05 12:00:00')]:
            self.assertEqual(display_number(raw, fmt, False), expected)
        for raw, fmt in [('1', ';;;'), ('1', '[Red]0'), ('1', '0;[Red]-0'), ('NaN', 'General'), ('1e999', 'General'), ('40729.5', 'yyyy-mm-dd'), ('0.5000000000000000000000000000001', 'h:mm:ss')]:
            with self.assertRaisesRegex(ValueError, 'XLSX_NUMBER_FORMAT_UNRESOLVED'):
                display_number(raw, fmt, False)

    def test_text_cells_with_hidden_format_are_not_exempt(self):
        def formats(root):
            root.find(q('numFmts'))[0].set('formatCode', ';;;')
        def target(root):
            cell(root, 'A2').set('s', '3')
            cell(root, 'D2').set('s', '0')
        self.partial(rewrite({'xl/styles.xml': formats, 'xl/worksheets/sheet1.xml': target}), 'XLSX_NUMBER_FORMAT_UNRESOLVED')

    def test_effective_style_visibility_background_and_unknown_alignment(self):
        for mutate in [
            lambda root: root.find(q('fonts'))[0].find(q('color')).set('rgb', 'FFFFFFFF'),
            lambda root: root.find(q('fills'))[0][0].set('patternType', 'solid'),
            lambda root: root.find(q('fonts'))[0].find(q('sz')).set('val', '0'),
            lambda root: ET.SubElement(root.find(q('cellXfs'))[0], q('alignment'), textRotation='90'),
        ]:
            self.partial(rewrite({'xl/styles.xml': mutate}))

    def test_hidden_sheet_row_column_zero_dimensions_and_hidden_zeroes(self):
        self.partial(rewrite({'xl/workbook.xml': lambda root: root.find(q('sheets'))[1].set('state', 'veryHidden')}), 'XLSX_VISIBILITY_UNRESOLVED')
        for mutate in [
            lambda root: root.find(q('sheetData'))[1].set('hidden', '1'),
            lambda root: root.find(q('sheetData'))[1].set('ht', '0'),
            lambda root: root.find(q('sheetFormatPr')).set('zeroHeight', '1'),
            lambda root: root.find(q('sheetViews'))[0].set('showZeros', '0'),
            lambda root: ET.SubElement(ET.SubElement(root, q('cols')), q('col'), min='1', max='2', hidden='1'),
            lambda root: ET.SubElement(ET.SubElement(root, q('cols')), q('col'), min='1', max='2', width='0'),
        ]:
            self.partial(rewrite({'xl/worksheets/sheet1.xml': mutate}), 'XLSX_VISIBILITY_UNRESOLVED')

    def test_conditional_formatting_filters_drawings_headers_and_unknown_elements(self):
        for name in ('conditionalFormatting', 'autoFilter', 'drawing', 'headerFooter', 'legacyDrawing', 'extLst', 'unknown'):
            self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: ET.SubElement(root, q(name))}), 'XLSX_MARKUP_UNSUPPORTED')

    def test_cell_row_column_style_and_metadata_are_not_silently_ignored(self):
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: cell(root, 'B2').set('s', '999')}), 'XLSX_STYLE_UNRESOLVED')
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: cell(root, 'B2').set('cm', '1')}), 'XLSX_CELL_METADATA_UNRESOLVED')
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: root.find(q('sheetData'))[1].set('s', '1')}), 'XLSX_VISIBILITY_UNRESOLVED')

    def test_duplicate_cells_wrong_rows_and_invalid_reference_cannot_relabel_values(self):
        for address in ('A2', 'B3', 'b2', 'XFE2', 'B0', 'B1048577'):
            with self.assertRaises(XlsxExtractionError):
                extract(rewrite({'xl/worksheets/sheet1.xml': lambda root: cell(root, 'B2').set('r', address)}))

    def test_merge_overlap_missing_anchor_and_nonanchor_content_are_partial(self):
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: ET.SubElement(root.find(q('mergeCells')), q('mergeCell'), ref='B4:D4')}), 'XLSX_MERGE_UNRESOLVED')
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: root.find(q('mergeCells'))[0].set('ref', 'A5:C5')}), 'XLSX_MERGE_UNRESOLVED')
        def content(root):
            row = root.find(q('sheetData'))[-1]
            ET.SubElement(ET.SubElement(ET.SubElement(row, q('c'), r='B4', t='inlineStr'), q('is')), q('t')).text = 'contradictory hidden cell'
        self.partial(rewrite({'xl/worksheets/sheet1.xml': content}), 'XLSX_MERGE_UNRESOLVED')

    def test_shared_strings_rich_runs_escapes_and_missing_indices(self):
        def strings(parts):
            parts['xl/sharedStrings.xml'] = f'<sst xmlns="{S}" count="1" uniqueCount="1"><si><r><rPr><b/></rPr><t>批次</t></r><r><t xml:space="preserve"> _x005F_x0041_</t></r></si></sst>'.encode()
        def types(root):
            ET.SubElement(root, f'{{{CT}}}Override', PartName='/xl/sharedStrings.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml')
        def rels(root):
            ET.SubElement(root, f'{{{R}}}Relationship', Id='sst', Type=O + 'sharedStrings', Target='sharedStrings.xml')
        def target(root):
            item = cell(root, 'A2'); item.remove(item[0]); item.set('t', 's'); ET.SubElement(item, q('v')).text = '0'
        result = extract(rewrite({'[Content_Types].xml': types, 'xl/_rels/workbook.xml.rels': rels, 'xl/worksheets/sheet1.xml': target}, strings))
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(result.blocks[5].text, '批次 _x0041_')
        self.partial(rewrite({'xl/worksheets/sheet1.xml': target}), 'XLSX_SHARED_STRING_UNRESOLVED')

    def test_rich_run_colors_and_direct_unknown_attributes_are_partial(self):
        def mutate(root):
            string = cell(root, 'A2')[0]; string.remove(string[0])
            run = ET.SubElement(string, q('r')); ET.SubElement(ET.SubElement(run, q('rPr')), q('color'), rgb='FFFFFFFF'); ET.SubElement(run, q('t')).text = 'white'
        self.partial(rewrite({'xl/worksheets/sheet1.xml': mutate}), 'XLSX_MARKUP_UNSUPPORTED')
        self.partial(rewrite({'xl/worksheets/sheet1.xml': lambda root: cell(root, 'B2').set('{urn:unknown}semantic', '1')}), 'XLSX_MARKUP_UNSUPPORTED')

    def test_iso_dates_boolean_errors_and_nonfinite_numbers(self):
        def iso(root):
            target = cell(root, 'D2'); target.set('t', 'd'); target[0].text = '2026-09-08T12:30:15'
        self.assertEqual(extract(rewrite({'xl/worksheets/sheet1.xml': iso})).blocks[8].text, '2026-09-08 12:30:15')
        for kind, value in [('e', '#DIV/0!'), ('n', 'NaN'), ('b', 'yes'), ('d', '2026-02-30')]:
            def mutate(root):
                target = cell(root, 'B2'); target.set('t', kind); target[0].text = value
            self.partial(rewrite({'xl/worksheets/sheet1.xml': mutate}))

    def test_external_links_xml_entities_and_duplicate_zip_entries_are_rejected(self):
        def external(root):
            ET.SubElement(root, f'{{{R}}}Relationship', Id='external', Type=O + 'externalLink', Target='https://example.invalid/file.xlsx', TargetMode='External')
        with self.assertRaisesRegex(XlsxExtractionError, 'XLSX_EXTERNAL_RELATIONSHIP'):
            extract(rewrite({'xl/_rels/workbook.xml.rels': external}))
        def entity(parts):
            parts['xl/workbook.xml'] = f'<!DOCTYPE workbook [<!ENTITY bad "value">]><workbook xmlns="{S}">&bad;</workbook>'.encode()
        with self.assertRaisesRegex(XlsxExtractionError, 'XLSX_XML_UNSAFE_OR_CORRUPT'):
            extract(rewrite(edit=entity))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            duplicated = rewrite(duplicates=[('xl/workbook.xml', '<fake/>')])
        with self.assertRaisesRegex(XlsxExtractionError, 'XLSX_PACKAGE_ENTRY_LIMIT'):
            extract(duplicated)

    def test_unlisted_sheet_wrong_type_root_and_reused_sheet_id(self):
        self.partial(rewrite({'xl/workbook.xml': lambda root: root.find(q('sheets')).remove(root.find(q('sheets'))[1])}), 'XLSX_SHEET_UNRESOLVED')
        self.partial(rewrite({'[Content_Types].xml': lambda root: next(node for node in root if node.get('PartName') == '/xl/worksheets/sheet2.xml').set('ContentType', 'application/xml')}), 'XLSX_PART_UNSUPPORTED')
        with self.assertRaisesRegex(XlsxExtractionError, 'XLSX_SHEET_IDENTITY_INVALID'):
            extract(rewrite({'xl/workbook.xml': lambda root: root.find(q('sheets'))[1].set('sheetId', '1')}))
        self.partial(rewrite({'xl/worksheets/sheet2.xml': lambda root: setattr(root, 'tag', q('chartsheet'))}), 'XLSX_PART_UNSUPPORTED')

    def test_resource_budgets_and_source_hash_cannot_return_partial_success_prefix(self):
        raw = ORIGINAL.read_bytes()
        for limits, code in [(XlsxLimits(cells=14), 'XLSX_CELL_BUDGET_EXCEEDED'),
            (XlsxLimits(sheets=1), 'XLSX_SHEET_BUDGET_EXCEEDED'),
            (XlsxLimits(characters=10), 'XLSX_TEXT_BUDGET_EXCEEDED'),
            (XlsxLimits(package=PackageLimits(xml_nodes=10)), 'XLSX_XML_BUDGET_EXCEEDED')]:
            with self.assertRaisesRegex(XlsxExtractionError, code):
                extract(raw, limits=limits)
        with self.assertRaisesRegex(XlsxExtractionError, 'XLSX_SOURCE_SHA_MISMATCH'):
            extract_xlsx(raw, expected_sha256='a' * 64)

    def test_token_identity_changes_even_when_formatted_text_matches(self):
        first = extract(ORIGINAL.read_bytes())
        second = extract(rewrite({'xl/worksheets/sheet1.xml': lambda root: setattr(cell(root, 'B2')[0], 'text', '42.500')}))
        self.assertEqual(first.blocks[6].text, second.blocks[6].text)
        self.assertNotEqual(first.blocks[6].token_sha256, second.blocks[6].token_sha256)
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)


if __name__ == '__main__':
    unittest.main()
