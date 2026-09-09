"""Bounded cell display semantics: no formula evaluation or generic formatter."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'


def q(name):
    return f'{{{S}}}{name}'


BUILTIN_FORMATS = {0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00',
    9: '0%', 10: '0.00%', 14: 'mm-dd-yy', 20: 'h:mm', 21: 'h:mm:ss',
    22: 'm/d/yy h:mm', 49: '@'}
DATE_FORMATS = {'mm-dd-yy', 'yyyy-mm-dd', 'yyyy/m/d', 'yyyy/mm/dd', 'yyyy-m-d',
                'm/d/yy', 'mm/dd/yy', 'mm/dd/yyyy', 'm/d/yyyy', 'yyyy"年"m"月"d"日"'}
DATETIME_FORMATS = {'yyyy-mm-dd h:mm:ss', 'yyyy-mm-dd hh:mm:ss', 'yyyy-mm-dd h:mm',
                    'yyyy-mm-dd hh:mm', 'm/d/yy h:mm', 'yyyy-mm-dd h:mm:ss.000'}
TIME_FORMATS = {'h:mm', 'hh:mm', 'h:mm:ss', 'hh:mm:ss'}
SIMPLE_NUMBER_FORMAT = r'(0{1,20}|#,##0)(?:\.(0{1,8}))?(%)?(?:"([^"\r\n\x00-\x1f]{1,40})")?'


def supported_format(code):
    return code in DATE_FORMATS | DATETIME_FORMATS | TIME_FORMATS | {'General', '@'} or (isinstance(code, str) and re.fullmatch(SIMPLE_NUMBER_FORMAT, code) is not None)


def display_number(raw, code, date1904):
    """Canonical values. Dates are ISO, numerics follow the supported format.

    Stored XML remains bound by token/source hashes. Excel's fictitious day 60
    and unsupported locale/conditional formats cannot be promoted to evidence.
    """
    with localcontext() as ctx:
        ctx.prec = 160
        return _display_number(raw, code, date1904)


def _display_number(raw, code, date1904):
    try:
        if not isinstance(raw, str) or len(raw) > 128 or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,3})?', raw):
            raise ValueError
        number = Decimal(raw)
        if not number.is_finite() or abs(number.adjusted()) > 100:
            raise ValueError
        if code in DATE_FORMATS | DATETIME_FORMATS | TIME_FORMATS:
            if not 0 <= number < 2_958_465 or (not date1904 and int(number) == 60):
                raise ValueError
            if code in TIME_FORMATS and number >= 1:
                raise ValueError
            if code not in TIME_FORMATS and not date1904 and number < 1:
                raise ValueError
            epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 31)
            if not date1904 and number >= 61:
                number -= 1
            seconds = number * 86400
            # Preserve millisecond precision only; never round into another day.
            if seconds * 1000 != (seconds * 1000).to_integral_value():
                raise ValueError
            value = epoch + timedelta(milliseconds=int(seconds * 1000))
            if code in DATE_FORMATS:
                if value.time().isoformat() != '00:00:00':
                    raise ValueError
                return value.date().isoformat()
            if code in TIME_FORMATS:
                return value.time().isoformat(timespec='seconds')
            return value.isoformat(sep=' ', timespec='milliseconds' if value.microsecond else 'seconds')
        if code in {'General', '@'}:
            # Canonical full stored precision; not an Excel viewport rendering.
            result = format(number, 'f')
            return result.rstrip('0').rstrip('.') if '.' in result else result
        match = re.fullmatch(SIMPLE_NUMBER_FORMAT, code)
        if match is None:
            raise ValueError
        integer, decimal, percent, unit = match.groups()
        places = len(decimal or '')
        with localcontext() as ctx:
            ctx.prec = 128
            value = (number * (100 if percent else 1)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        result = format(value, f',.{places}f' if integer.startswith('#') else f'.{places}f')
        if integer.startswith('0') and len(integer) > 1:
            sign = '-' if result.startswith('-') else ''
            result = sign + result.lstrip('-').zfill(len(integer) + (places + 1 if places else 0))
        return result + ('%' if percent else '') + (unit or '')
    except (ValueError, OverflowError, InvalidOperation):
        raise ValueError('XLSX_NUMBER_FORMAT_UNRESOLVED') from None


class CellStyles:
    def __init__(self, root, theme, debt, part='xl/styles.xml'):
        self.root, self.debt, self.part = root, debt, part
        self.formats = dict(BUILTIN_FORMATS)
        self.fonts, self.fills, self.borders, self.bases, self.cells = [], [], [], [], []
        self.black_theme = False
        if theme is not None:
            dark = theme.find(f'{{{A}}}themeElements/{{{A}}}clrScheme/{{{A}}}dk1')
            if dark is not None and len(dark) == 1:
                node = dark[0]
                self.black_theme = ((node.tag == f'{{{A}}}sysClr' and node.get('lastClr') == '000000') or (node.tag == f'{{{A}}}srgbClr' and node.get('val') == '000000')) and not list(node)
        if root is None:
            return
        for node in root.iter():
            if (node.text or '').strip() or (node.tail or '').strip():
                self.fail('XLSX_STYLE_UNRESOLVED')
        for node in root:
            name = node.tag.rsplit('}', 1)[-1]
            if node.tag != q(name) or name not in {'numFmts', 'fonts', 'fills', 'borders', 'cellStyleXfs', 'cellXfs', 'cellStyles', 'dxfs', 'tableStyles', 'colors'}:
                self.fail('XLSX_STYLE_UNRESOLVED')
                continue
            allowed_attrs = {'count', 'defaultTableStyle', 'defaultPivotStyle'} if name == 'tableStyles' else ({'count'} if name != 'colors' else set())
            if set(node.attrib) - allowed_attrs or (node.text or '').strip() or (node.tail or '').strip():
                self.fail('XLSX_STYLE_UNRESOLVED')
            if 'count' in node.attrib and (re.fullmatch(r'0|[1-9][0-9]{0,5}', node.get('count', '')) is None or int(node.get('count')) != len(node)):
                self.fail('XLSX_STYLE_UNRESOLVED')
            expected_child = {'fonts': 'font', 'fills': 'fill', 'borders': 'border', 'cellStyleXfs': 'xf', 'cellXfs': 'xf', 'cellStyles': 'cellStyle'}.get(name)
            if expected_child and any(child.tag != q(expected_child) for child in node):
                self.fail('XLSX_STYLE_UNRESOLVED')
            if name == 'numFmts':
                seen = set()
                for fmt in node:
                    try:
                        index = int(fmt.get('numFmtId', ''))
                        if index < 164 or index in seen or fmt.tag != q('numFmt') or set(fmt.attrib) != {'numFmtId', 'formatCode'} or list(fmt):
                            raise ValueError
                        self.formats[index] = fmt.get('formatCode')
                        seen.add(index)
                    except (ValueError, TypeError):
                        self.fail('XLSX_STYLE_UNRESOLVED')
            if name == 'fonts': self.fonts = list(node)
            if name == 'fills': self.fills = list(node)
            if name == 'borders': self.borders = list(node)
            if name == 'cellStyleXfs': self.bases = list(node)
            if name == 'cellXfs': self.cells = list(node)
            if name in {'dxfs', 'tableStyles'} and list(node):
                self.fail('XLSX_STYLE_UNRESOLVED')
            if name == 'colors':
                # An unused palette is metadata. Every effective indexed color
                # remains unresolved in _visibility, regardless of this table.
                if len(node) != 1 or node[0].tag != q('indexedColors') or node[0].attrib or len(node[0]) > 256:
                    self.fail('XLSX_STYLE_UNRESOLVED')
                else:
                    for color in node[0]:
                        if color.tag != q('rgbColor') or set(color.attrib) != {'rgb'} or re.fullmatch(r'[0-9A-Fa-f]{8}', color.get('rgb', '')) is None or list(color):
                            self.fail('XLSX_STYLE_UNRESOLVED')
        if root.attrib or len({node.tag for node in root}) != len(root):
            self.fail('XLSX_STYLE_UNRESOLVED')
        if not self.cells or not self.bases or not self.fonts or not self.fills:
            self.fail('XLSX_STYLE_UNRESOLVED')
        self.checked = {}

    def fail(self, reason):
        self.debt(reason, self.part, '/styleSheet')

    def resolve(self, index):
        if self.root is None:
            if index != 0:
                self.fail('XLSX_STYLE_UNRESOLVED')
            return 'General'
        if index in self.checked:
            return self.checked[index]
        try:
            if not 0 <= index < len(self.cells):
                raise ValueError
            xf = self.cells[index]
            base_id = int(xf.get('xfId', '0'))
            if not 0 <= base_id < len(self.bases):
                raise ValueError
            base = self.bases[base_id]
            for item in (base, xf):
                if item.tag != q('xf') or set(item.attrib) - {'numFmtId', 'fontId', 'fillId', 'borderId', 'xfId', 'pivotButton', 'quotePrefix', 'applyNumberFormat', 'applyFont', 'applyFill', 'applyBorder', 'applyAlignment', 'applyProtection'}:
                    raise ValueError
                for key, value in item.attrib.items():
                    if key.startswith('apply') or key in {'pivotButton', 'quotePrefix'}:
                        if value not in {'0', '1', 'true', 'false'}:
                            raise ValueError
                for node in item:
                    if node.tag == q('alignment'):
                        if list(node) or set(node.attrib) - {'horizontal', 'vertical', 'textRotation', 'wrapText', 'shrinkToFit', 'indent', 'relativeIndent', 'justifyLastLine', 'readingOrder'} or node.get('textRotation', '0') != '0' or node.get('readingOrder', '0') != '0':
                            raise ValueError
                    elif node.tag != q('protection') or list(node) or set(node.attrib) - {'locked', 'hidden'}:
                        raise ValueError
                font_id = int(item.get('fontId', base.get('fontId', '0')))
                fill_id = int(item.get('fillId', base.get('fillId', '0')))
                if not 0 <= font_id < len(self.fonts) or not 0 <= fill_id < len(self.fills):
                    raise ValueError
                self._visibility(self.fonts[font_id], self.fills[fill_id])
            code_id = int(xf.get('numFmtId', base.get('numFmtId', '0')))
            if xf.get('applyNumberFormat') in {'0', 'false'} and code_id != int(base.get('numFmtId', '0')):
                raise ValueError
            code = self.formats.get(code_id)
            if code is None:
                raise ValueError
            self.checked[index] = code
            return code
        except (ValueError, TypeError, IndexError):
            self.fail('XLSX_STYLE_UNRESOLVED')
            return 'General'

    def _visibility(self, font, fill):
        # Only plain dark text on a plain light background is currently proved.
        # Font substitution and cell-width clipping are outside native semantics.
        if font.tag != q('font') or font.attrib or fill.tag != q('fill') or fill.attrib or len(fill) != 1:
            raise ValueError
        for node in font:
            if node.tag not in {q(name) for name in ('name', 'charset', 'family', 'b', 'i', 'u', 'strike', 'sz', 'scheme', 'color')} or list(node) or set(node.attrib) - ({'rgb', 'theme', 'auto'} if node.tag == q('color') else {'val'}):
                self.fail('XLSX_VISIBILITY_UNRESOLVED')
            if node.tag == q('sz'):
                try:
                    if not 4 <= Decimal(node.get('val', '0')) <= 409:
                        raise ValueError
                except (ValueError, InvalidOperation):
                    self.fail('XLSX_VISIBILITY_UNRESOLVED')
            if node.tag == q('color') and not (node.attrib in ({'rgb': 'FF000000'}, {'rgb': '00000000'}, {'auto': '1'}, {'auto': 'true'}) or (node.attrib == {'theme': '1'} and self.black_theme)):
                self.fail('XLSX_VISIBILITY_UNRESOLVED')
        pattern = fill[0]
        if pattern.tag != q('patternFill') or pattern.attrib.get('patternType', 'none') != 'none' or set(pattern.attrib) - {'patternType'} or list(pattern):
            self.fail('XLSX_VISIBILITY_UNRESOLVED')
