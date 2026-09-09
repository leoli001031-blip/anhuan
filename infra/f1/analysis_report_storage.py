"""Candidate-only MinIO service identities. Default 0014 storage is unchanged.

The ingestion identity belongs only to the task-authorized source gateway.
Workers hold no object-store credentials.
MinIO API: https://github.com/minio/minio-py/blob/master/minio/minioadmin.py
"""
from __future__ import annotations
import os
from pathlib import Path
from pathlib import Path
from minio import Minio
from minio.credentials import StaticProvider
from minio.minioadmin import MinioAdmin

STORAGE_ROLES = ('api', 'ingestion', 'worker')
STORAGE_SECRET_NAMES = tuple(f'minio_{role}_{kind}' for role in STORAGE_ROLES for kind in ('user', 'password'))
BUCKETS = ('anhuan-f1-documents', 'anhuan-f1-quarantine', 'anhuan-f1-previews')


def service_policy(role: str) -> dict:
    if role not in STORAGE_ROLES:
        raise ValueError('STORAGE_ROLE_INVALID')
    buckets = BUCKETS[:2] if role == 'worker' else BUCKETS
    statements = [{'Effect':'Allow', 'Action':['s3:GetObject'], 'Resource':[f'arn:aws:s3:::{b}/*' for b in buckets]}]
    writes = BUCKETS if role == 'api' else BUCKETS[2:] if role == 'ingestion' else ()
    if writes:
        actions = ['s3:PutObject', 's3:AbortMultipartUpload', 's3:ListMultipartUploadParts']
        if role == 'api': actions.append('s3:DeleteObject')
        statements.append({'Effect':'Allow', 'Action':actions, 'Resource':[f'arn:aws:s3:::{b}/*' for b in writes]})
    statements.append({'Effect':'Allow','Action':['s3:GetBucketLocation'], 'Resource':[f'arn:aws:s3:::{b}' for b in buckets]})
    return {'Version':'2012-10-17','Statement':statements}


def provision(endpoint: str, root_user: str, root_password: str, identities: dict[str, tuple[str, str]]) -> None:
    if set(identities) != set(STORAGE_ROLES) or len({user for user, _ in identities.values()}) != len(STORAGE_ROLES):
        raise ValueError('STORAGE_IDENTITY_SET_INVALID')
    if any(user == root_user or password == root_password for user, password in identities.values()):
        raise ValueError('STORAGE_ROOT_IDENTITY_FORBIDDEN')
    client = Minio(endpoint, access_key=root_user, secret_key=root_password, secure=False)
    admin = MinioAdmin(endpoint=endpoint, credentials=StaticProvider(root_user,root_password), secure=False)
    for bucket in BUCKETS:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    for role in STORAGE_ROLES:
        user, password = identities[role]
        policy = f'anhuan-material-{role}-v1'
        admin.user_add(user,password)
        admin.policy_add(policy,policy=service_policy(role))
        admin.policy_set(policy,user=user)


def main() -> int:
    if os.environ.get('F1_LOCAL_ENGINEERING') != '1':
        print('STORAGE_PROVISIONING_DISABLED');return 2
    from platform_foundation.f1.secret_files import read_f1_secret_text
    try:
        # The one-shot provisioner alone mounts administrator and service keys.
        identities={role:(read_f1_secret_text(f'minio_{role}_user', file_env=f'F1_MINIO_{role.upper()}_USER_FILE'),read_f1_secret_text(f'minio_{role}_password', file_env=f'F1_MINIO_{role.upper()}_PASSWORD_FILE')) for role in STORAGE_ROLES}
        root=Path('/run/minio-root')
        provision(os.environ['MINIO_ENDPOINT'],(root/'minio_root_user').read_text().strip(),
                  (root/'minio_root_password').read_text().strip(),identities)
    except Exception:
        print('STORAGE_PROVISIONING_FAILED');return 1
    print('STORAGE_SERVICE_IDENTITIES_READY');return 0


if __name__ == '__main__':
    raise SystemExit(main())
