"""Bounded native cell evidence from original XLSX bytes.

No Excel engine, formula evaluation, macro execution or network access. A single
unresolved semantic debt prevents all workbook fragments from becoming usable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re

from .contracts import XlsxCellsLocator, _cell, canonical_json
from .docx_native import CoverageDebt
from .ooxml_package import O, R, PackageError, PackageLimits, read_package
from .xlsx_styles import A, S, CellStyles, display_number, supported_format, q

PARSER_VERSION = 'xlsx-native-1'
SUPPORT_PROFILE = 'transitional-visible-cells-no-formulas-1'
MAIN_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'
PREFIX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'


class XlsxExtractionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class XlsxLimits:
    package: PackageLimits = PackageLimits()
    sheets: int = 32
    cells: int = 20_000
    characters: int = 2_000_000
    cell_characters: int = 32_767
    merges_per_sheet: int = 512

    def __post_init__(self):
        if type(self.package) is not PackageLimits or any(type(getattr(self, key)) is not int or getattr(self, key) < 1 for key in ('sheets', 'cells', 'characters', 'cell_characters', 'merges_per_sheet')):
            raise ValueError('XLSX_LIMITS_INVALID')


@dataclass(frozen=True, slots=True, repr=False)
class XlsxBlock:
    locator: XlsxCellsLocator
    text: str = field(repr=False)
    token_sha256: str

    @property
    def body_sha256(self):
        return hashlib.sha256(self.text.encode()).hexdigest()

    def __repr__(self):
        return f'XlsxBlock(locator={self.locator!r}, text=<redacted>)'


@dataclass(frozen=True, slots=True)
class XlsxExtraction:
    source_sha256: str
    manifest_sha256: str
    blocks: tuple[XlsxBlock, ...]
    debts: tuple[CoverageDebt, ...]
    expected_block_count: int
    processed_block_count: int
    parser_version: str = PARSER_VERSION
    support_profile: str = SUPPORT_PROFILE

    @property
    def coverage_state(self):
        return 'partial' if self.debts else 'complete'

    @property
    def report_source_eligible(self):
        return not self.debts and any(block.text.strip() for block in self.blocks)


def _uint(value, maximum, *, zero=False):
    if not isinstance(value, str) or re.fullmatch(r'0|[1-9][0-9]{0,9}', value) is None:
        raise XlsxExtractionError('XLSX_STRUCTURE_INVALID')
    result = int(value)
    if not (0 if zero else 1) <= result <= maximum:
        raise XlsxExtractionError('XLSX_STRUCTURE_INVALID')
    return result


def _value(node):
    if node is None:
        return ''
    return node.text or ''


def _tree(node):
    return (node.tag, tuple(sorted(node.attrib.items())), node.text or '',
            tuple(_tree(child) for child in node))


def _range(value):
    if not isinstance(value, str):
        raise XlsxExtractionError('XLSX_RANGE_INVALID')
    pieces = value.split(':')
    try:
        if len(pieces) not in {1, 2}:
            raise ValueError
        start, end = _cell(pieces[0]), _cell(pieces[-1])
        if start[0] > end[0] or start[1] > end[1]:
            raise ValueError
        return start, end
    except ValueError:
        raise XlsxExtractionError('XLSX_RANGE_INVALID') from None


def extract_xlsx(source: bytes, *, expected_sha256: str, limits=XlsxLimits()):
    try:
        source_sha, parts, roots, types, edges, manifest = read_package(source, expected_sha256, limits=limits.package)
    except PackageError as exc:
        raise XlsxExtractionError(str(exc).replace('OOXML_', 'XLSX_', 1)) from None
    debts = []

    def debt(reason, part, path):
        value = CoverageDebt(reason, part, path)
        if value not in debts:
            if len(debts) >= 256:
                raise XlsxExtractionError('XLSX_COVERAGE_BUDGET_EXCEEDED')
            debts.append(value)

    def grammar(node, part, path, attrs=(), children=(), text=False):
        if set(node.attrib) - set(attrs) or any(child.tag not in {q(name) for name in children} for child in node) or (not text and (node.text or '').strip()):
            debt('XLSX_MARKUP_UNSUPPORTED', part, path)
        if any((child.tail or '').strip() for child in node):
            debt('XLSX_MARKUP_UNSUPPORTED', part, path)

    workbook = roots.get('xl/workbook.xml')
    office = [(origin, target) for origin, _rid, kind, target in edges if kind == O + 'officeDocument']
    if office != [('', 'xl/workbook.xml')] or workbook is None or workbook.tag != q('workbook') or types.get('xl/workbook.xml') != MAIN_TYPE:
        raise XlsxExtractionError('XLSX_PROFILE_UNSUPPORTED')
    policy = {
        O + 'worksheet': ('xl/workbook.xml', PREFIX + 'worksheet+xml', q('worksheet')),
        O + 'styles': ('xl/workbook.xml', PREFIX + 'styles+xml', q('styleSheet')),
        O + 'sharedStrings': ('xl/workbook.xml', PREFIX + 'sharedStrings+xml', q('sst')),
        O + 'theme': ('xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.theme+xml', f'{{{A}}}theme'),
        R + '/metadata/core-properties': ('', 'application/vnd.openxmlformats-package.core-properties+xml', '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties'),
        O + 'extended-properties': ('', 'application/vnd.openxmlformats-officedocument.extended-properties+xml', '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Properties'),
    }
    accepted, by_kind, incoming = set(), {}, {}
    relations = {}
    for origin, rid, kind, target in edges:
        incoming.setdefault(target, []).append((origin, kind))
        if kind == O + 'officeDocument':
            continue
        p = policy.get(kind)
        if p is None or origin != p[0] or types.get(target) != p[1] or roots.get(target) is None or roots[target].tag != p[2]:
            debt('XLSX_PART_UNSUPPORTED', target, '/relationships')
            continue
        accepted.add(target)
        by_kind.setdefault(kind, []).append(target)
        if origin == 'xl/workbook.xml':
            relations[rid] = (kind, target)
    for kind, targets in by_kind.items():
        if kind != O + 'worksheet' and len(targets) != 1:
            debt('XLSX_PART_UNSUPPORTED', 'xl/workbook.xml', '/duplicate-part-role')
    for name in parts:
        if name == '[Content_Types].xml':
            continue
        if name.endswith('.rels'):
            if types.get(name) != 'application/vnd.openxmlformats-package.relationships+xml':
                debt('XLSX_PART_UNSUPPORTED', name, '/content-type')
            continue
        if name != 'xl/workbook.xml' and (name not in accepted or len(incoming.get(name, [])) != 1):
            debt('XLSX_PART_UNSUPPORTED', name, '/')
    for name, root in roots.items():
        for node in root.iter():
            if node.tag == MC + 'AlternateContent' or any(MC + attr in node.attrib for attr in ('MustUnderstand', 'ProcessContent', 'PreserveElements', 'PreserveAttributes')):
                debt('XLSX_MARKUP_UNSUPPORTED', name, '/compatibility')
            if node.tag in {q('f'), q('calculatedColumnFormula'), q('totalsRowFormula')}:
                debt('XLSX_FORMULA_UNRESOLVED', name, '/formula')
            if node.tag == q('extLst'):
                debt('XLSX_MARKUP_UNSUPPORTED', name, '/extensions')

    def role(kind):
        names = by_kind.get(O + kind, [])
        return (names[0], roots[names[0]]) if len(names) == 1 else (None, None)

    grammar(workbook, 'xl/workbook.xml', '/workbook', {MC + 'Ignorable'}, {'fileVersion', 'workbookPr', 'workbookProtection', 'bookViews', 'sheets', 'definedNames', 'calcPr'})
    if len({node.tag for node in workbook}) != len(workbook):
        debt('XLSX_STRUCTURE_UNRESOLVED', 'xl/workbook.xml', '/workbook/duplicate')
    date1904 = False
    for node in workbook:
        if node.tag == q('workbookPr'):
            grammar(node, 'xl/workbook.xml', '/workbookPr', {'date1904', 'dateCompatibility', 'showObjects', 'updateLinks', 'codeName', 'defaultThemeVersion'})
            flag = node.get('date1904', '0')
            if flag not in {'0', '1', 'true', 'false'} or node.get('dateCompatibility', '1') not in {'1', 'true'}:
                debt('XLSX_DATE_SYSTEM_UNRESOLVED', 'xl/workbook.xml', '/workbookPr')
            date1904 = flag in {'1', 'true'}
        elif node.tag == q('workbookProtection'):
            grammar(node, 'xl/workbook.xml', '/workbookProtection')
        elif node.tag == q('definedNames'):
            grammar(node, 'xl/workbook.xml', '/definedNames')
        elif node.tag == q('calcPr'):
            grammar(node, 'xl/workbook.xml', '/calcPr', {'calcId', 'calcMode', 'fullCalcOnLoad', 'forceFullCalc', 'calcOnSave', 'concurrentCalc', 'refMode', 'iterate', 'iterateCount', 'iterateDelta', 'fullPrecision'})
            if node.get('fullPrecision', '1') not in {'1', 'true'}:
                debt('XLSX_NUMBER_FORMAT_UNRESOLVED', 'xl/workbook.xml', '/calcPr/fullPrecision')
        elif node.tag == q('fileVersion'):
            grammar(node, 'xl/workbook.xml', '/fileVersion', {'appName', 'lastEdited', 'lowestEdited', 'rupBuild', 'codeName'})
        elif node.tag == q('bookViews'):
            grammar(node, 'xl/workbook.xml', '/bookViews', children={'workbookView'})
            for view in node:
                grammar(view, 'xl/workbook.xml', '/workbookView', {'visibility', 'minimized', 'showHorizontalScroll', 'showVerticalScroll', 'showSheetTabs', 'xWindow', 'yWindow', 'windowWidth', 'windowHeight', 'tabRatio', 'firstSheet', 'activeTab', 'autoFilterDateGrouping'})
                if view.get('visibility', 'visible') != 'visible':
                    debt('XLSX_VISIBILITY_UNRESOLVED', 'xl/workbook.xml', '/workbookView')

    styles_part, style_root = role('styles')
    _theme_part, theme_root = role('theme')
    styles = CellStyles(style_root, theme_root, debt, styles_part or 'xl/styles.xml')
    strings_part, strings_root = role('sharedStrings')

    def text_item(item, part, path):
        grammar(item, part, path, children={'t', 'r'})
        if len(item.findall(q('t'))) > 1 or (item.findall(q('t')) and item.findall(q('r'))):
            debt('XLSX_TEXT_UNRESOLVED', part, path)
        texts = []
        for entry in item:
            if entry.tag == q('t'):
                runs = [entry]
            elif entry.tag == q('r'):
                grammar(entry, part, path, children={'rPr', 't'})
                runs = entry.findall(q('t'))
                if len(runs) != 1 or len(entry.findall(q('rPr'))) > 1:
                    debt('XLSX_TEXT_UNRESOLVED', part, path)
                for prop in entry.findall(q('rPr')):
                    grammar(prop, part, path, children={'b', 'i', 'u', 'rFont', 'sz', 'family', 'charset', 'scheme'})
                    for value in prop:
                        grammar(value, part, path, {'val'})
                        if value.tag == q('sz'):
                            try:
                                if not 4 <= Decimal(value.get('val', '0')) <= 409: raise ValueError
                            except (ValueError, InvalidOperation):
                                debt('XLSX_VISIBILITY_UNRESOLVED', part, path)
            else:
                continue
            for run in runs:
                grammar(run, part, path, {'{http://www.w3.org/XML/1998/namespace}space'}, text=True)
                raw = run.text or ''
                if raw.strip() != raw and run.get('{http://www.w3.org/XML/1998/namespace}space') != 'preserve':
                    debt('XLSX_WHITESPACE_UNRESOLVED', part, path)
                try:
                    # One pass preserves escaped literal escape sequences.
                    decoded = re.sub(r'_x([0-9a-fA-F]{4})_', lambda m: chr(int(m[1], 16)), raw)
                    decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
                    if any(ord(char) < 32 and char not in '\t\n\r' for char in decoded):
                        raise ValueError
                    texts.append(decoded)
                except (ValueError, UnicodeError):
                    debt('XLSX_TEXT_UNRESOLVED', part, path)
        value = ''.join(texts)
        if len(value) > limits.cell_characters:
            raise XlsxExtractionError('XLSX_TEXT_BUDGET_EXCEEDED')
        return value

    strings = []
    string_characters = 0
    if strings_root is not None:
        grammar(strings_root, strings_part, '/sst', {'count', 'uniqueCount'}, {'si'})
        for item in strings_root:
            if len(strings) >= limits.cells:
                raise XlsxExtractionError('XLSX_CELL_BUDGET_EXCEEDED')
            value = text_item(item, strings_part, f'/sst/si[{len(strings) + 1}]')
            string_characters += len(value)
            if string_characters > limits.characters:
                raise XlsxExtractionError('XLSX_TEXT_BUDGET_EXCEEDED')
            strings.append(value)

    sheets_node = workbook.find(q('sheets'))
    if sheets_node is None or not 1 <= len(sheets_node) <= limits.sheets:
        raise XlsxExtractionError('XLSX_SHEET_BUDGET_EXCEEDED')
    grammar(sheets_node, 'xl/workbook.xml', '/sheets', children={'sheet'})
    sheets, ids, names, targets = [], set(), set(), set()
    for sheet in sheets_node:
        grammar(sheet, 'xl/workbook.xml', '/sheets/sheet', {'name', 'sheetId', 'state', '{' + O.rstrip('/') + '}id'})
        sid = _uint(sheet.get('sheetId'), 2**32 - 1)
        name = sheet.get('name')
        relation = relations.get(sheet.get('{' + O.rstrip('/') + '}id'))
        if not relation or relation[0] != O + 'worksheet':
            debt('XLSX_SHEET_UNRESOLVED', 'xl/workbook.xml', '/sheets/sheet')
            continue
        part = relation[1]
        try:
            XlsxCellsLocator(sid, name, part, 'A1')
        except ValueError:
            raise XlsxExtractionError('XLSX_SHEET_IDENTITY_INVALID') from None
        if sid in ids or name.casefold() in names or part in targets:
            raise XlsxExtractionError('XLSX_SHEET_IDENTITY_INVALID')
        ids.add(sid); names.add(name.casefold()); targets.add(part)
        if sheet.get('state', 'visible') != 'visible':
            debt('XLSX_VISIBILITY_UNRESOLVED', part, '/sheet/state')
        sheets.append((sid, name, part, roots[part]))
    if targets != set(by_kind.get(O + 'worksheet', [])):
        debt('XLSX_SHEET_UNRESOLVED', 'xl/workbook.xml', '/sheets/unlisted')
    expected = sum(1 for name in by_kind.get(O + 'worksheet', []) for node in roots[name].iter(q('c')))
    if expected > limits.cells:
        raise XlsxExtractionError('XLSX_CELL_BUDGET_EXCEEDED')
    blocks, characters = [], 0
    for sid, sheet_name, part, root in sheets:
        grammar(root, part, '/worksheet', {MC + 'Ignorable'}, {'sheetPr', 'dimension', 'sheetViews', 'sheetFormatPr', 'cols', 'sheetData', 'mergeCells', 'pageMargins', 'pageSetup', 'printOptions'})
        if len({node.tag for node in root}) != len(root):
            debt('XLSX_STRUCTURE_UNRESOLVED', part, '/worksheet/duplicate')
        for node in root:
            _sheet_metadata(node, part, debt, grammar)
        merges = []
        for container in root.findall(q('mergeCells')):
            grammar(container, part, '/mergeCells', {'count'}, {'mergeCell'})
            if len(container) > limits.merges_per_sheet:
                raise XlsxExtractionError('XLSX_MERGE_BUDGET_EXCEEDED')
            for merge in container:
                grammar(merge, part, '/mergeCells/mergeCell', {'ref'})
                ref = merge.get('ref')
                start, end = _range(ref)
                if start == end:
                    debt('XLSX_MERGE_UNRESOLVED', part, '/mergeCells')
                    continue
                if any(start[0] <= b[0] and a[0] <= end[0] and start[1] <= b[1] and a[1] <= end[1] for a, b, _ref in merges):
                    debt('XLSX_MERGE_UNRESOLVED', part, '/mergeCells')
                merges.append((start, end, ref))
        data = root.find(q('sheetData'))
        if data is None:
            debt('XLSX_STRUCTURE_UNRESOLVED', part, '/sheetData')
            continue
        grammar(data, part, '/sheetData', children={'row'})
        last_row = 0
        anchors = set()
        for row in data:
            grammar(row, part, '/row', {'r', 'spans', 's', 'customFormat', 'ht', 'hidden', 'customHeight', 'outlineLevel', 'collapsed', 'thickTop', 'thickBot', 'ph'}, {'c'})
            row_no = _uint(row.get('r'), 1_048_576)
            if row_no <= last_row:
                raise XlsxExtractionError('XLSX_CELL_ORDER_INVALID')
            last_row = row_no
            if row.get('hidden', '0') not in {'0', 'false'} or row.get('s', '0') != '0' or row.get('collapsed', '0') not in {'0', 'false'}:
                debt('XLSX_VISIBILITY_UNRESOLVED', part, f'/row[{row_no}]')
            if 'ht' in row.attrib:
                _positive_dimension(row.get('ht'), part, debt)
            previous_column = 0
            for cell in row:
                grammar(cell, part, '/cell', {'r', 's', 't', 'cm', 'vm', 'ph'}, {'v', 'is', 'f'})
                address = cell.get('r', '')
                try:
                    cr, column = _cell(address)
                except ValueError:
                    raise XlsxExtractionError('XLSX_CELL_ADDRESS_INVALID') from None
                if cr != row_no or column <= previous_column:
                    raise XlsxExtractionError('XLSX_CELL_ORDER_INVALID')
                previous_column = column
                path = f'/sheetData/{address}'
                if any(key in cell.attrib for key in ('cm', 'vm')) or cell.get('ph', '0') not in {'0', 'false'}:
                    debt('XLSX_CELL_METADATA_UNRESOLVED', part, path)
                code = styles.resolve(_uint(cell.get('s', '0'), 65535, zero=True))
                if not supported_format(code):
                    debt('XLSX_NUMBER_FORMAT_UNRESOLVED', part, path)
                values, inline = cell.findall(q('v')), cell.findall(q('is'))
                kind = cell.get('t', 'n')
                if len(values) > 1 or len(inline) > 1 or (values and inline):
                    debt('XLSX_CELL_VALUE_UNRESOLVED', part, path)
                for value in values:
                    grammar(value, part, path, text=True)
                raw = _value(values[0]) if values else ''
                text = ''
                if cell.find(q('f')) is not None or kind == 'str':
                    debt('XLSX_FORMULA_UNRESOLVED', part, path)
                elif kind == 'inlineStr':
                    if len(inline) != 1 or values:
                        debt('XLSX_CELL_VALUE_UNRESOLVED', part, path)
                    else:
                        text = text_item(inline[0], part, path)
                elif inline:
                    debt('XLSX_CELL_VALUE_UNRESOLVED', part, path)
                elif kind == 's':
                    index = _uint(raw, limits.cells, zero=True)
                    if index >= len(strings):
                        debt('XLSX_SHARED_STRING_UNRESOLVED', part, path)
                    else:
                        text = strings[index]
                elif kind == 'b':
                    if raw not in {'0', '1'}:
                        debt('XLSX_CELL_VALUE_UNRESOLVED', part, path)
                    else:
                        text = 'TRUE' if raw == '1' else 'FALSE'
                elif kind == 'd':
                    try:
                        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)?', raw):
                            raise ValueError
                        date.fromisoformat(raw) if len(raw) == 10 else datetime.fromisoformat(raw)
                        text = raw.replace('T', ' ')
                    except ValueError:
                        debt('XLSX_DATE_SYSTEM_UNRESOLVED', part, path)
                elif kind == 'n':
                    if raw:
                        try:
                            text = display_number(raw, code, date1904)
                        except ValueError:
                            debt('XLSX_NUMBER_FORMAT_UNRESOLVED', part, path)
                else:
                    debt('XLSX_CELL_VALUE_UNRESOLVED', part, path)
                ref = address
                for start, end, merged_range in merges:
                    if start[0] <= cr <= end[0] and start[1] <= column <= end[1]:
                        if (cr, column) == start:
                            ref = merged_range
                            anchors.add(merged_range)
                        elif text.strip():
                            debt('XLSX_MERGE_UNRESOLVED', part, path)
                characters += len(text)
                if characters > limits.characters or len(text) > limits.cell_characters:
                    raise XlsxExtractionError('XLSX_TEXT_BUDGET_EXCEEDED')
                location = XlsxCellsLocator(sid, sheet_name, part, ref)
                tokens = {'cell': _tree(cell), 'number_format': code, 'date1904': date1904}
                blocks.append(XlsxBlock(location, text, hashlib.sha256(canonical_json(tokens)).hexdigest()))
        if anchors != {ref for _start, _end, ref in merges}:
            debt('XLSX_MERGE_UNRESOLVED', part, '/mergeCells/missing-anchor')
    if len(blocks) != expected:
        debt('XLSX_STRUCTURE_UNRESOLVED', 'xl/workbook.xml', '/cell-coverage')
    identity = {'source_sha256': source_sha, 'parser_version': PARSER_VERSION,
        'support_profile': SUPPORT_PROFILE, 'parts': manifest,
        'blocks': [{'locator': block.locator.to_dict(), 'tokens': block.token_sha256, 'body': block.body_sha256} for block in blocks],
        'debts': [{'reason': item.reason_code, 'part': item.part, 'path': item.path} for item in debts]}
    return XlsxExtraction(source_sha, hashlib.sha256(canonical_json(identity)).hexdigest(),
        () if debts else tuple(blocks), tuple(debts), expected, len(blocks))


def _positive_dimension(value, part, debt):
    try:
        if not isinstance(value, str) or len(value) > 32 or not Decimal(value).is_finite() or Decimal(value) <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        debt('XLSX_VISIBILITY_UNRESOLVED', part, '/dimensions')


def _sheet_metadata(node, part, debt, grammar):
    name = node.tag.rsplit('}', 1)[-1]
    if name == 'dimension':
        grammar(node, part, '/dimension', {'ref'})
        _range(node.get('ref'))
    elif name == 'sheetFormatPr':
        grammar(node, part, '/sheetFormatPr', {'baseColWidth', 'defaultColWidth', 'defaultRowHeight', 'customHeight', 'zeroHeight', 'thickTop', 'thickBottom', 'outlineLevelRow', 'outlineLevelCol'})
        if node.get('zeroHeight', '0') not in {'0', 'false'}:
            debt('XLSX_VISIBILITY_UNRESOLVED', part, '/sheetFormatPr')
        for key in ('defaultColWidth', 'defaultRowHeight'):
            if key in node.attrib:
                _positive_dimension(node.get(key), part, debt)
    elif name == 'cols':
        grammar(node, part, '/cols', children={'col'})
        last = 0
        for col in node:
            grammar(col, part, '/cols/col', {'min', 'max', 'width', 'style', 'hidden', 'bestFit', 'customWidth', 'phonetic', 'outlineLevel', 'collapsed'})
            start, end = _uint(col.get('min'), 16384), _uint(col.get('max'), 16384)
            if start > end or start <= last:
                debt('XLSX_STRUCTURE_UNRESOLVED', part, '/cols')
            last = end
            if col.get('hidden', '0') not in {'0', 'false'} or col.get('style', '0') != '0' or col.get('collapsed', '0') not in {'0', 'false'}:
                debt('XLSX_VISIBILITY_UNRESOLVED', part, '/cols/col')
            if 'width' in col.attrib:
                _positive_dimension(col.get('width'), part, debt)
    elif name == 'pageMargins':
        grammar(node, part, '/pageMargins', {'left', 'right', 'top', 'bottom', 'header', 'footer'})
    elif name == 'pageSetup':
        grammar(node, part, '/pageSetup', {'paperSize', 'scale', 'firstPageNumber', 'fitToWidth', 'fitToHeight', 'pageOrder', 'orientation', 'usePrinterDefaults', 'blackAndWhite', 'draft', 'cellComments', 'useFirstPageNumber', 'errors', 'horizontalDpi', 'verticalDpi', 'copies'})
    elif name == 'printOptions':
        grammar(node, part, '/printOptions', {'horizontalCentered', 'verticalCentered', 'headings', 'gridLines', 'gridLinesSet'})
    elif name == 'sheetPr':
        grammar(node, part, '/sheetPr', {'codeName', 'filterMode', 'enableFormatConditionsCalculation', 'published', 'syncHorizontal', 'syncVertical', 'syncRef', 'transitionEvaluation', 'transitionEntry'}, {'outlinePr', 'pageSetUpPr', 'tabColor'})
        if node.get('filterMode', '0') not in {'0', 'false'} or node.get('transitionEvaluation', '0') not in {'0', 'false'}:
            debt('XLSX_VISIBILITY_UNRESOLVED', part, '/sheetPr')
        for child in node:
            attrs = {'summaryBelow', 'summaryRight', 'showOutlineSymbols', 'applyStyles'} if child.tag == q('outlinePr') else ({'autoPageBreaks', 'fitToPage'} if child.tag == q('pageSetUpPr') else {'rgb', 'theme', 'indexed', 'auto', 'tint'})
            grammar(child, part, '/sheetPr/option', attrs)
            if child.tag == q('outlinePr') and child.get('applyStyles', '0') not in {'0', 'false'}:
                debt('XLSX_STYLE_UNRESOLVED', part, '/sheetPr/outlinePr')
    elif name == 'sheetViews':
        grammar(node, part, '/sheetViews', children={'sheetView'})
        for view in node:
            grammar(view, part, '/sheetView', {'windowProtection', 'showFormulas', 'showGridLines', 'showRowColHeaders', 'showZeros', 'rightToLeft', 'tabSelected', 'showRuler', 'showOutlineSymbols', 'defaultGridColor', 'showWhiteSpace', 'view', 'topLeftCell', 'colorId', 'zoomScale', 'zoomScaleNormal', 'zoomScaleSheetLayoutView', 'zoomScalePageLayoutView', 'workbookViewId'}, {'selection', 'pane'})
            if view.get('showZeros', '1') not in {'1', 'true'} or view.get('rightToLeft', '0') not in {'0', 'false'}:
                debt('XLSX_VISIBILITY_UNRESOLVED', part, '/sheetView')
            for option in view:
                grammar(option, part, '/sheetView/option', {'pane', 'activeCell', 'activeCellId', 'sqref'} if option.tag == q('selection') else {'xSplit', 'ySplit', 'topLeftCell', 'activePane', 'state'})
