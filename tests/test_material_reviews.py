"""Human revision validation and actual AEAD substitution boundaries."""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from platform_foundation.f1.features.material_rag import security
from platform_foundation.f1.features.evidence.contracts import PdfPageLocator,DocxBlockLocator,XlsxCellsLocator,ImageLocator
from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload,decrypt_review_fragment,request_digest

class MaterialReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        key=Path(temp.name)/'key';key.write_text('ab'*32);key.chmod(0o600)
        key_patch=patch.object(security,'MATERIAL_RAG_KEY_FILE',key);key_patch.start();self.addCleanup(key_patch.stop)
        self.source={k:str(uuid.uuid4()) for k in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','base_revision_id')}
        self.source.update(source_sha256='a'*64,base_manifest_sha256='b'*64)
    def request(self,locator):
        self.source.update(source_format=locator.source_format,base_fragments=[dict(id=str(uuid.uuid4()),locator=locator.to_dict())])
        return ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=None,action='confirm',base_revision_id=self.source['base_revision_id'],
            base_manifest_sha256=self.source['base_manifest_sha256'],checked_against_source=True,
            texts=[dict(base_fragment_id=self.source['base_fragments'][0]['id'],text='保留原样  24 mg/L\n😀')],
            fields=[dict(base_fragment_id=self.source['base_fragments'][0]['id'],field_name='report_title',text='人工修订')])
    def row(self,payload,n=0):
        return {**self.source,**payload['fragments'][n],'review_revision_id':payload['revision_id']}
    def test_all_formats_keep_typed_locations_exact_body_and_stable_retry(self):
        for locator in (PdfPageLocator(9),DocxBlockLocator(3),XlsxCellsLocator(1,'排口😀','xl/worksheets/sheet1.xml','B2:C4'),ImageLocator(80,40,40,80,'c'*64,6)):
            with self.subTest(format=locator.source_format):
                request=self.request(locator);p=build_review_payload(self.source,request);again=build_review_payload(self.source,request)
                self.assertEqual(p['request_sha256'],request_digest(self.source,request));self.assertEqual(p['request_sha256'],again['request_sha256'])
                self.assertNotEqual(p['fragments'][0]['body_ciphertext_hex'],again['fragments'][0]['body_ciphertext_hex'])
                self.assertEqual(decrypt_review_fragment(self.row(p)),request.texts[0].text)
                self.assertEqual(decrypt_review_fragment(self.row(p,1)),'人工修订')
    def test_identity_and_ciphertext_substitution_rejected(self):
        request=self.request(DocxBlockLocator(1));payload=build_review_payload(self.source,request);row=self.row(payload)
        for key in ('enterprise_id','knowledge_scope_id','document_version_id','review_revision_id','base_revision_id','base_fragment_id'):
            with self.subTest(key=key),self.assertRaises(ValueError):decrypt_review_fragment({**row,key:str(uuid.uuid4())})
        for key in ('source_sha256','base_manifest_sha256','body_sha256'):
            with self.subTest(key=key),self.assertRaises(ValueError):decrypt_review_fragment({**row,key:'0'*64})
        wrong=copy.deepcopy(row);wrong['body_ciphertext_hex']=wrong['body_ciphertext_hex'][:-2]+'ff'
        with self.assertRaises(Exception):decrypt_review_fragment(wrong)
    def test_requires_full_source_check_and_no_duplicate_or_unknown_fields(self):
        request=self.request(DocxBlockLocator(1));data=request.model_dump()
        for changed in (dict(checked_against_source=False),dict(checked_against_source=1),dict(texts=[]),dict(texts=data['texts']*2),dict(fields=[{**data['fields'][0],'field_name':'admin'}]),dict(fields=data['fields']*2),dict(texts=[{**data['texts'][0],'text':'bad\x00text'}])):
            with self.subTest(changed=changed),self.assertRaises(ValidationError):ReviewWriteIn(**{**data,**changed})
    def test_missing_or_reordered_base_positions_cannot_be_confirmed(self):
        request=self.request(DocxBlockLocator(1));self.source['base_fragments'].append(dict(id=str(uuid.uuid4()),locator=DocxBlockLocator(2).to_dict()))
        with self.assertRaisesRegex(ValueError,'REVIEW_COVERAGE_INVALID'):build_review_payload(self.source,request)
    def test_revoke_requires_head_and_cannot_carry_text(self):
        request=self.request(DocxBlockLocator(1))
        with self.assertRaises(ValidationError):ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=None,action='revoke')
        revoke=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=uuid.uuid4(),action='revoke')
        self.assertEqual(build_review_payload(self.source,revoke)['fragments'],[])
        with self.assertRaises(ValidationError):ReviewWriteIn(**{**revoke.model_dump(),'texts':request.texts})
