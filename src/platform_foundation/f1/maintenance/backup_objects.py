"""Bounded logical MinIO archive, preserving original multipart identities."""
from __future__ import annotations

import hashlib
import re

from minio.datatypes import Part

from .object_reconcile import BUCKETS

MAX_OBJECTS = 10000
MAX_OBJECT_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
ETAG = re.compile(r'^[0-9a-f]{32}(?:-([1-9][0-9]{0,3}))?$')


class ObjectArchiveError(RuntimeError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def md5(raw):
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def inventory(client):
    result = []
    total = 0
    for bucket in BUCKETS:
        if not client.bucket_exists(bucket):
            raise ObjectArchiveError('BACKUP_BUCKET_MISSING')
        if client.get_bucket_versioning(bucket).status is not None:
            raise ObjectArchiveError('BACKUP_VERSIONED_BUCKET_UNSUPPORTED')
        for obj in client.list_objects(bucket, recursive=True):
            total += obj.size
            if (len(result) >= MAX_OBJECTS or not 0 <= obj.size <= MAX_OBJECT_BYTES
                    or total > MAX_TOTAL_BYTES):
                raise ObjectArchiveError('BACKUP_OBJECT_BUDGET_EXCEEDED')
            result.append({'bucket': bucket, 'key': obj.object_name, 'etag': obj.etag,
                           'size': obj.size, 'modified': obj.last_modified.isoformat()})
    return sorted(result, key=lambda x: (x['bucket'], x['key']))


def _headers(obj):
    headers = {'Content-Type': obj.content_type or 'application/octet-stream'}
    for name, value in obj.metadata.items():
        lower = name.lower()
        if lower.startswith('x-amz-meta-'):
            headers[lower] = value
        elif (lower.startswith('x-amz-server-side-encryption') or lower in {
                'x-amz-object-lock-mode', 'x-amz-object-lock-retain-until-date',
                'x-amz-object-lock-legal-hold', 'content-encoding', 'content-language',
                'content-disposition', 'cache-control', 'expires'}):
            raise ObjectArchiveError('BACKUP_OBJECT_METADATA_UNSUPPORTED')
    return headers


def export_objects(client, write):
    """write(name, bytes) writes an exclusive private package member."""
    before = inventory(client)
    exported = []
    for index, item in enumerate(before):
        bucket, key = item['bucket'], item['key']
        stat = client.stat_object(bucket, key)
        match = ETAG.fullmatch(item['etag'])
        if match is None or (stat.etag, stat.size) != (item['etag'], item['size']):
            raise ObjectArchiveError('BACKUP_OBJECT_IDENTITY_INVALID')
        if client.get_object_tags(bucket, key):
            raise ObjectArchiveError('BACKUP_OBJECT_TAGS_UNSUPPORTED')
        headers = _headers(stat)
        count = int(match.group(1) or '1')
        if count > 128:
            raise ObjectArchiveError('BACKUP_PART_BUDGET_EXCEEDED')
        parts = []
        full = hashlib.sha256()
        for number in range(1, count + 1):
            # S3 partNumber reads the original completed-upload part, including
            # nonuniform part sizes. A Range guess would lose the ETag identity.
            response = client.get_object(bucket, key, request_headers={'If-Match': '"'+item['etag']+'"'},
                extra_query_params={'partNumber': str(number)} if match.group(1) else None)
            try:
                raw = response.read(MAX_OBJECT_BYTES + 1)
            finally:
                response.close()
                response.release_conn()
            if len(raw) > MAX_OBJECT_BYTES:
                raise ObjectArchiveError('BACKUP_OBJECT_BUDGET_EXCEEDED')
            name = f'object-{index:05d}-part-{number:04d}.bin'
            write(name, raw)
            full.update(raw)
            parts.append({'file': name, 'size': len(raw), 'sha256': sha(raw), 'md5': md5(raw)})
        archived = {**item, 'headers': headers, 'multipart': bool(match.group(1)),
                    'sha256': full.hexdigest(), 'parts': parts}
        validate_object(archived)
        exported.append(archived)
    if inventory(client) != before:
        raise ObjectArchiveError('BACKUP_OBJECTS_CHANGED')
    return exported


def validate_object(item):
    if set(item) != {'bucket','key','etag','size','modified','headers','multipart','sha256','parts'}:
        raise ObjectArchiveError('BACKUP_OBJECT_MANIFEST_INVALID')
    if (item['bucket'] not in BUCKETS or not isinstance(item['key'], str)
            or not 1 <= len(item['key'].encode()) <= 1024 or '\x00' in item['key']
            or type(item['size']) is not int or not 0 <= item['size'] <= MAX_OBJECT_BYTES
            or type(item['multipart']) is not bool or not isinstance(item['parts'], list)
            or not 1 <= len(item['parts']) <= 128
            or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
            or not isinstance(item['headers'], dict) or not item['headers']):
        raise ObjectArchiveError('BACKUP_OBJECT_MANIFEST_INVALID')
    for name, value in item['headers'].items():
        if (not (name == 'Content-Type' or re.fullmatch(r'x-amz-meta-[a-z0-9_-]+', name))
                or not isinstance(value, str) or len(value) > 8192 or '\r' in value or '\n' in value):
            raise ObjectArchiveError('BACKUP_OBJECT_METADATA_INVALID')
    for part in item['parts']:
        if (set(part) != {'file','size','sha256','md5'}
                or not re.fullmatch(r'object-[0-9]{5}-part-[0-9]{4}\.bin', part['file'])
                or type(part['size']) is not int or not 0 <= part['size'] <= MAX_OBJECT_BYTES
                or not re.fullmatch(r'[0-9a-f]{64}', part['sha256'])
                or not re.fullmatch(r'[0-9a-f]{32}', part['md5'])):
            raise ObjectArchiveError('BACKUP_PART_MANIFEST_INVALID')
    parts = item['parts']
    if sum(p['size'] for p in parts) != item['size']:
        raise ObjectArchiveError('BACKUP_PART_SIZE_MISMATCH')
    if item['multipart']:
        if any(p['size'] < 5*1024*1024 for p in parts[:-1]):
            raise ObjectArchiveError('BACKUP_MULTIPART_BOUNDARY_INVALID')
        expected = md5(b''.join(bytes.fromhex(p['md5']) for p in parts)) + '-' + str(len(parts))
    else:
        if len(parts) != 1:
            raise ObjectArchiveError('BACKUP_PART_COUNT_MISMATCH')
        expected = parts[0]['md5']
    if expected != item['etag']:
        raise ObjectArchiveError('BACKUP_ETAG_NOT_REPRODUCIBLE')


def restore_objects(client, items, read, event):
    """Caller must validate the whole package and require empty destination buckets."""
    for item in items:
        bucket, key = item['bucket'], item['key']
        event({'stage': 'object_started', 'bucket': bucket, 'key_sha256': sha(key.encode())})
        if item['multipart']:
            # Pinned MinIO SDK exposes the S3 multipart primitives; high-level
            # put_object chooses uniform parts and cannot preserve arbitrary ones.
            upload = client._create_multipart_upload(bucket, key, dict(item['headers']))
            try:
                parts = []
                for number, part in enumerate(item['parts'], 1):
                    etag = client._upload_part(bucket, key, read(part['file']), None, upload, number)
                    if etag != part['md5']:
                        raise ObjectArchiveError('RESTORE_PART_ETAG_MISMATCH')
                    parts.append(Part(number, etag))
                result = client._complete_multipart_upload(bucket, key, upload, parts)
            except BaseException:
                client._abort_multipart_upload(bucket, key, upload)
                raise
        else:
            result = client._put_object(bucket, key, read(item['parts'][0]['file']), dict(item['headers']))
        if result.etag != item['etag']:
            raise ObjectArchiveError('RESTORE_OBJECT_ETAG_MISMATCH')
        response = client.get_object(bucket, key)
        try:
            raw = response.read(item['size'] + 1)
        finally:
            response.close()
            response.release_conn()
        stat = client.stat_object(bucket, key)
        if (len(raw) != item['size'] or sha(raw) != item['sha256']
                or stat.etag != item['etag'] or _headers(stat) != item['headers']):
            raise ObjectArchiveError('RESTORE_OBJECT_READBACK_MISMATCH')
        event({'stage': 'object_verified', 'bucket': bucket, 'key_sha256': sha(key.encode())})
