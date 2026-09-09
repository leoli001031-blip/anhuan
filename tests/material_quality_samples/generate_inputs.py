"""Rebuild only these synthetic fixtures. Gold below is authored, never parser output."""
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).parent
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
O = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def package(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, text in sorted(parts.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode())
    return out.getvalue()


def inputs():
    pdf = PdfWriter()
    for text in ['SYNTHETIC discharge inspection: COD 42.50 mg/L; mass 1.25 kg.',
                 'SYNTHETIC second inspection: COD 38.25 mg/L; mass 2.50 kg.']:
        page = pdf.add_blank_page(620, 800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): pdf._add_object(font)})})
        content = DecodedStreamObject(); content.set_data(f'BT /F1 10 Tf 20 740 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = pdf._add_object(content)
    out = io.BytesIO(); pdf.write(out)
    docx = package({
        '[Content_Types].xml': f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="{R}"><Relationship Id="rId1" Type="{O}officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>合成排口记录 COD 42.50 mg/L</w:t></w:r></w:p><w:p><w:r><w:t>合成危废记录 1.25 kg</w:t></w:r></w:p><w:tbl><w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>合成复测 COD 38.25 mg/L</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
    xlsx = package({
        '[Content_Types].xml': f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="{R}"><Relationship Id="rId1" Type="{O}officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{R}"><Relationship Id="rId1" Type="{O}worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{O}worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{O[:-1]}"><sheets><sheet name="合成排口" sheetId="1" r:id="rId1"/><sheet name="合成危废" sheetId="2" r:id="rId2"/></sheets></workbook>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>COD mg/L</t></is></c><c r="B1"><v>42.5</v></c></row></sheetData></worksheet>',
        'xl/worksheets/sheet2.xml': f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>质量 kg</t></is></c><c r="B1"><v>1.25</v></c></row></sheetData></worksheet>'})
    image = Image.new('RGB', (600, 160), 'white')
    ImageDraw.Draw(image).text((20, 30), 'SYNTHETIC COD 42.50 mg/L; mass 1.25 kg', fill='black')
    jpg = io.BytesIO(); image.save(jpg, 'JPEG', quality=95, subsampling=0)
    return {'pdf': out.getvalue(), 'docx': docx, 'xlsx': xlsx, 'jpeg': jpg.getvalue()}


def main():
    values = inputs()
    for fmt, body in values.items():
        (ROOT / ('sample.' + fmt)).write_bytes(body)
    (ROOT / 'corrupt.bin').write_bytes(b'Not a material file\x00')
    # Identity expected from a simple independent Pillow JPEG re-encode; no evidence parser used.
    out = io.BytesIO()
    with Image.open(io.BytesIO(values['jpeg'])) as image:
        image.convert('RGB').save(out, 'JPEG', quality=95, subsampling=0, optimize=False, progressive=False)
    rendered_hash = digest(out.getvalue())
    gold = {
        'pdf': [{'text': 'SYNTHETIC discharge inspection: COD 42.50 mg/L; mass 1.25 kg.', 'locator': {'schema_version': 2, 'kind': 'pdf_page', 'page_number': 1}},
                {'text': 'SYNTHETIC second inspection: COD 38.25 mg/L; mass 2.50 kg.', 'locator': {'schema_version': 2, 'kind': 'pdf_page', 'page_number': 2}}],
        'docx': [{'text': '合成排口记录 COD 42.50 mg/L', 'locator': {'schema_version': 2, 'kind': 'docx_block', 'body_index': 1, 'row_index': None, 'cell_index': None, 'grid_column': None, 'grid_span': None, 'paragraph_index': None, 'part': 'word/document.xml'}},
                 {'text': '合成危废记录 1.25 kg', 'locator': {'schema_version': 2, 'kind': 'docx_block', 'body_index': 2, 'row_index': None, 'cell_index': None, 'grid_column': None, 'grid_span': None, 'paragraph_index': None, 'part': 'word/document.xml'}},
                 {'text': '合成复测 COD 38.25 mg/L', 'locator': {'schema_version': 2, 'kind': 'docx_block', 'body_index': 3, 'row_index': 1, 'cell_index': 1, 'grid_column': 1, 'grid_span': 1, 'paragraph_index': 1, 'part': 'word/document.xml'}}],
        'xlsx': [{'text': text, 'locator': {'schema_version': 2, 'kind': 'xlsx_cells', 'sheet_id': sid, 'sheet_name': name, 'part': f'xl/worksheets/sheet{sid}.xml', 'cell_range': address}} for sid, name, address, text in [(1, '合成排口', 'A1', 'COD mg/L'), (1, '合成排口', 'B1', '42.5'), (2, '合成危废', 'A1', '质量 kg'), (2, '合成危废', 'B1', '1.25')]],
        'jpeg': [{'text': 'SYNTHETIC COD 42.50 mg/L; mass 1.25 kg', 'locator': {'schema_version': 2, 'kind': 'image', 'source_width': 600, 'source_height': 160, 'rendered_width': 600, 'rendered_height': 160, 'rendered_sha256': rendered_hash, 'exif_orientation': 1}}]}
    cases = []
    for fmt in ('pdf', 'docx', 'xlsx', 'jpeg'):
        sample = {'id': fmt + '_positive', 'format': fmt, 'path': 'sample.' + fmt, 'sha256': digest(values[fmt]), 'gold': gold[fmt]}
        if fmt == 'jpeg':
            sample['synthetic_ocr'] = {'text': 'SYNTHETIC COD 42.50 mg/L; mass 1.25 kg', 'rendered_sha256': rendered_hash}
        cases.append(sample)
        bad = copy.deepcopy(sample); bad.update(id=fmt + '_corrupt', path='corrupt.bin', sha256=digest(b'Not a material file\x00'), expected_failure='PARSER_OR_INPUT_ERROR'); cases.append(bad)
    for name, check in [('number', 'numbers_exact'), ('unit', 'units_exact'), ('omission', 'no_omissions'), ('location', 'locations_exact')]:
        bad = copy.deepcopy(cases[0]); bad.update(id='gold_' + name + '_negative', expected_failure=check)
        if name == 'number': bad['gold'][0]['text'] = bad['gold'][0]['text'].replace('42.50', '425.0')
        if name == 'unit': bad['gold'][0]['text'] = bad['gold'][0]['text'].replace('mg/L', 'kg')
        if name == 'omission': bad['gold'][0]['text'] = bad['gold'][0]['text'].replace('discharge ', 'discharge unextracted-note ')
        if name == 'location': bad['gold'][0]['locator']['page_number'] = 3
        cases.append(bad)
    (ROOT / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'scope': 'synthetic', 'gold_provenance': 'Manually authored expectations from fixture specification; not extracted by parser or model. JPEG text is a fixed transport stub.', 'samples': cases}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
