"""Typed evidence attribution survives generation, revisions and artifact rendering."""
from __future__ import annotations
import dataclasses
import hashlib
import unittest
import uuid
from platform_foundation.f1.features.evidence.contracts import PdfPageLocator,DocxBlockLocator,XlsxCellsLocator,ImageLocator
from platform_foundation.f1.features.analysis_reports.contracts import EvidenceUnit, EligibleSource, FrozenSourceSet,TEMPLATE_ID
from platform_foundation.f1.features.analysis_reports.repository import fingerprint_for
from platform_foundation.f1.features.analysis_reports.generator import EvidenceDrivenReportGenerator
from platform_foundation.f1.features.analysis_reports.artifact import render_html_artifact,ReportArtifactInvalid
from platform_foundation.f1.features.analysis_reports.pdf_artifact import render_pdf_artifact
import pypdfium2 as pdfium

class EffectiveEvidenceTests(unittest.TestCase):
    def test_original_view_reopens_each_format_at_exact_location(self):
        from platform_foundation.f1.features.evidence.original import render_original
        from tests.test_material_pdf_renderer import layout_pdf
        from tests.test_native_evidence import package,p,extract
        from tests.test_xlsx_native_evidence import ORIGINAL,extract as extract_sheet
        from tests.test_jpeg_native_evidence import jpeg
        from platform_foundation.f1.features.material_intake.jpeg_renderer import render_jpeg
        cases=[]
        raw=package(p('原件 COD 42 mg/L'))
        cases.append((raw,extract(raw).blocks[0].locator,'原件 COD 42 mg/L'))
        raw=ORIGINAL.read_bytes();block=next(b for b in extract_sheet(raw).blocks if b.text.strip())
        cases.append((raw,block.locator,block.text))
        cases.append((layout_pdf(pages=2),PdfPageLocator(2),None))
        raw=jpeg(6);rendered=render_jpeg(raw,expected_sha256=hashlib.sha256(raw).hexdigest())
        cases.append((raw,ImageLocator(rendered.source_width,rendered.source_height,rendered.width,rendered.height,rendered.image_sha256,rendered.exif_orientation),None))
        for raw,locator,expected in cases:
            with self.subTest(fmt=locator.source_format):
                row=dict(document_version_id=str(uuid.uuid4()),source_format=locator.source_format,source_sha256=hashlib.sha256(raw).hexdigest(),locator=locator.to_dict())
                result=render_original(row,raw)
                self.assertEqual(result['locator'],locator.to_dict());self.assertEqual(result['original_text'],expected)
                if expected is None:self.assertTrue(result['image'].startswith('data:image/jpeg;base64,'))
                self.assertEqual(set(result),{'document_version_id','source_sha256','locator','location','original_text','image'})
        bad={**row,'locator':{**locator.to_dict(),'rendered_sha256':'0'*64}}
        with self.assertRaises(ValueError):render_original(bad,raw)

    def sources(self):
        locators=[PdfPageLocator(7),DocxBlockLocator(4,2,1,1,2,1),XlsxCellsLocator(2,'排放记录','xl/worksheets/sheet2.xml','C88'),ImageLocator(200,100,200,100,'a'*64,1)]
        result=[]
        for n,l in enumerate(locators):
            body=f'排口编号 ZX{n}9988 COD {17+n} mg/L，监测记录未签字。'
            unit=EvidenceUnit(page_number=getattr(l,'page_number',None),ordinal=1,text=body,
                body_sha256=hashlib.sha256(body.encode()).hexdigest(),locator=l.to_dict(),evidence_revision_id=uuid.uuid4(),fragment_id=uuid.uuid4())
            result.append(EligibleSource(document_version_id=uuid.uuid4(),document_name='样本.'+l.source_format,
                version_number=3,source_sha256='a'*64,scope_kind='service_provider' if n==0 else 'client',page_number=unit.page_number,evidence_units=(unit,)))
        return result
    def test_four_format_citations_and_export_text_match_real_locations(self):
        sources=self.sources();eid,client=uuid.uuid4(),uuid.uuid4()
        frozen=FrozenSourceSet(eid,client,TEMPLATE_ID,fingerprint_for(eid,client,sources),tuple(sources))
        generated=EvidenceDrivenReportGenerator().generate(frozen)
        self.assertEqual(len(generated.citations),4)
        payload={'version_number':1,'sections':[dataclasses.asdict(x) for x in generated.sections],
            'citations':[{**dataclasses.asdict(x),'document_version_id':str(x.document_version_id)} for x in generated.citations]}
        html=render_html_artifact(payload).body.decode();pdf=render_pdf_artifact(payload)
        with pdfium.PdfDocument(pdf.body) as document:
            pdf_text=''.join(page.get_textpage().get_text_range() for page in document)
        for citation,source in zip(generated.citations,sources):
            self.assertEqual(citation.fragment_id,source.evidence_units[0].fragment_id)
            self.assertEqual(citation.locator,source.evidence_units[0].locator)
            self.assertIn(citation.location,html);self.assertIn(citation.location,pdf_text)
            self.assertIn(citation.excerpt,html)
        self.assertNotIn('None',html);self.assertIn('C88',pdf_text);self.assertIn('图像全文',pdf_text)
        payload['citations'][1]['page_number']=1
        with self.assertRaises(ReportArtifactInvalid):render_html_artifact(payload)
    def test_same_text_changed_revision_or_position_changes_fingerprint(self):
        sources=self.sources();eid,client=uuid.uuid4(),uuid.uuid4();before=fingerprint_for(eid,client,sources)
        for updates in ({'evidence_revision_id':uuid.uuid4()},{'fragment_id':uuid.uuid4()},{'locator':DocxBlockLocator(9).to_dict()}):
            changed=[*sources];changed[1]=dataclasses.replace(sources[1],evidence_units=(dataclasses.replace(sources[1].evidence_units[0],**updates),))
            self.assertNotEqual(before,fingerprint_for(eid,client,changed))
    def test_native_evidence_cannot_masquerade_as_pdf_page(self):
        unit=self.sources()[1].evidence_units[0]
        with self.assertRaises(ValueError):dataclasses.replace(unit,page_number=1)
        with self.assertRaises(ValueError):dataclasses.replace(unit,evidence_revision_id=None)
