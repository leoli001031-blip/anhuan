"""Verify supplied backup keys against stored ciphertext without exposing bodies."""
from __future__ import annotations

import hashlib
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row

from ..features.material_rag import security
from ..features.evidence.contracts import FragmentIdentity,parse_locator
from ..features.evidence.review import ReviewFragmentIdentity
from ..features.material_intake.ocr import ocr_checkpoint_aad
from .. import ocr_cache


def decoded(raw):
    value=raw.decode('ascii').strip()
    return bytes.fromhex(value) if len(value)==64 else value.encode('ascii')


def verify_ciphertexts(c,keys):
    """Full current-material scan, including old/revoked revisions and OCR rows."""
    material=AESGCM(decoded(keys['f1_material_rag_key']))
    qa=AESGCM(decoded(keys['f1_qa_key']))
    counts={}
    def decrypt(cipher,aad,expected_aad,expected_body):
        if hashlib.sha256(aad).hexdigest()!=expected_aad or not bytes(cipher).startswith(security._MAGIC):
            raise ValueError('BACKUP_AAD_INVALID')
        raw=bytes(cipher)[len(security._MAGIC):]
        body=material.decrypt(raw[:12],raw[12:],aad)
        body.decode('utf-8','strict')
        if hashlib.sha256(body).hexdigest()!=expected_body:
            raise ValueError('BACKUP_BODY_DIGEST_INVALID')
    def rows(label,query):
        counts[label]=0
        with c.cursor(name='backup_crypto',row_factory=dict_row) as cursor:
            cursor.execute(query)
            for row in cursor:
                yield row
                counts[label]+=1
    for r in rows('native_fragments',
        'SELECT f.*,r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.source_sha256,r.parser_version,r.extraction_contract '
        'FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id AND r.enterprise_id=f.enterprise_id'):
        identity=FragmentIdentity(*(r[k] for k in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','extraction_revision_id')),
            r['source_sha256'],r['parser_version'],r['extraction_contract'],parse_locator(r['locator']),r['ordinal'],r['body_sha256'])
        if identity.id!=r['id'] or identity.locator.sha256!=r['locator_sha256']:
            raise ValueError('BACKUP_FRAGMENT_IDENTITY_INVALID')
        decrypt(r['body_ciphertext'],identity.aad(),r['body_aad_sha256'],r['body_sha256'])
    for r in rows('review_fragments',
        'SELECT f.*,r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.base_revision_id,r.base_manifest_sha256,r.source_sha256 '
        'FROM f1.material_review_fragment f JOIN f1.material_review_revision r ON r.id=f.review_revision_id AND r.enterprise_id=f.enterprise_id'):
        identity=ReviewFragmentIdentity(*(r[k] for k in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id',
            'review_revision_id','base_revision_id','base_fragment_id','base_manifest_sha256','source_sha256','entry_kind','field_name','ordinal')),
            parse_locator(r['locator']),r['body_sha256'])
        if identity.id!=r['id'] or identity.locator.sha256!=r['locator_sha256']:
            raise ValueError('BACKUP_REVIEW_IDENTITY_INVALID')
        decrypt(r['body_ciphertext'],identity.aad(),r['body_aad_sha256'],r['body_sha256'])
    for r in rows('pdf_units','SELECT * FROM f1.material_rag_unit'):
        aad=security.unit_aad_for_identity(unit_id=r['id'],**{k:r[k] for k in ('enterprise_id','knowledge_scope_id',
            'document_record_id','document_version_id','source_sha256','page_number','ordinal','parser_version','body_sha256')})
        decrypt(r['body_ciphertext'],aad,r['body_aad_sha256'],r['body_sha256'])
    for r in rows('dataset_references','SELECT * FROM f1.material_rag_scope_binding WHERE dataset_ref_ciphertext IS NOT NULL'):
        aad=security.dataset_ref_aad(enterprise_id=r['enterprise_id'],knowledge_scope_id=r['knowledge_scope_id'],binding_id=r['id'])
        decrypt(r['dataset_ref_ciphertext'],aad,r['dataset_ref_aad_sha256'],r['dataset_ref_sha256'])
    for r in rows('ocr_checkpoints','SELECT * FROM f1.material_ocr_checkpoint'):
        aad=ocr_checkpoint_aad(**{k:r[k] for k in ('enterprise_id','document_version_id','source_sha256','expected_page_count',
            'page_number','parser_backend','source_unit_id','body_sha256','character_count','confidence_mean_ppm','table_candidate','two_column_candidate')})
        decrypt(r['body_ciphertext'],aad,r['body_aad_sha256'],r['body_sha256'])
    for r in rows('ocr_cache','SELECT * FROM f1.material_ocr_result_cache'):
        e=r['envelope']
        aad=ocr_cache.canonical({'schema':'f1.ocr-result-cache.aad.v1','enterprise_id':str(r['enterprise_id']),
            'document_version_id':str(r['document_version_id']),'source_sha256':r['source_sha256'],'unit_no':r['unit_no'],
            'input_sha256':r['input_sha256'],'body_sha256':e['body_sha256']})
        decrypt(bytes.fromhex(e['ciphertext_hex']),aad,e['aad_sha256'],e['body_sha256'])
    for r in rows('qa_responses','SELECT * FROM f1.qa_request WHERE response_encrypted IS NOT NULL'):
        raw=bytes(r['response_encrypted'])
        # Independent v1/v2 persisted-wire check; no OIDC/runtime app import is
        # needed to verify a backup. A future wire change must update this gate.
        if raw.startswith(b'F1Q1'):
            raw=raw[4:]
            identity=f"{r['request_id']}\0{r['enterprise_id']}\0{r['question_sha256']}"
            context=r['query_context_sha256']
            aad=(('f1.qa.response.v1\0'+identity) if context=='0'*64
                 else ('f1.qa.response.v2\0'+identity+'\0'+context)).encode('ascii')
        elif int(r['attempt'] or 0)==0:
            aad=None
        else:
            raise ValueError('BACKUP_QA_ENVELOPE_INVALID')
        body=qa.decrypt(raw[:12],raw[12:],aad)
        json.loads(body)
        if r['response_sha256'] and hashlib.sha256(body).hexdigest()!=r['response_sha256']:
            raise ValueError('BACKUP_QA_DIGEST_INVALID')
    for label,table,key_name in [('f0i_key_verifiers','f0i.configuration','f0i_key'),
                               ('f0f_key_verifiers','f0f.body_configuration','f0f_source_key')]:
        count=c.execute('SELECT count(*) FROM '+table).fetchone()[0]
        counts[label]=count
        if count:
            if key_name not in keys:
                raise ValueError('BACKUP_HISTORICAL_KEY_REQUIRED')
            valid=c.execute("SELECT bool_and(encode(f0f_crypto.digest(f0f_crypto.pgp_sym_decrypt_bytea("
                "key_verifier_ciphertext,encode(%s::bytea,'hex')),'sha256'),'hex')=key_verifier_plaintext_sha256) FROM "+table,
                (keys[key_name],)).fetchone()[0]
            if valid is not True:
                raise ValueError('BACKUP_HISTORICAL_KEY_INVALID')
    return counts
