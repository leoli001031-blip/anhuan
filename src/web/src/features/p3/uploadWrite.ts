import { pendingWrite } from '../../adapters/pendingWrites';
import { IngestionApiError } from './ingestionApi';
import type { DocumentDetail, IngestionCapabilities, KnowledgeScopeTarget, VersionSummary } from './types';

export const MATERIAL_CONTENT_TYPES = ['application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'image/jpeg'];

export function uploadFileError(file: File, caps: IngestionCapabilities | null, accepted = MATERIAL_CONTENT_TYPES): string | null {
  if (!caps?.upload_enabled || caps.scanner.state !== 'ready') return '安全检查暂不可用，上传已暂停';
  const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  const format = caps.allowed_types.find(x => accepted.includes(x.content_type) && x.extensions.some(e => e.toLowerCase() === extension));
  if (!format) return '当前环境未开放此文件格式';
  if (file.size <= 0) return '文件为空';
  const nativeLimit = format.content_type === 'image/jpeg' ? 20 * 1024 * 1024
    : MATERIAL_CONTENT_TYPES.slice(1, 3).includes(format.content_type) ? 25 * 1024 * 1024 : Infinity;
  if (file.size > Math.min(format.max_file_bytes, caps.limits.max_file_bytes, nativeLimit)) return '文件超过当前格式的大小上限';
  return null;
}

// Only the digest and request UUID enter sessionStorage. The user reselects
// files after reload; neither file contents, names nor credentials are stored.
export async function pendingUpload(enterpriseId: string, subject: string, file: File,
  target: {displayName: string; scope: KnowledgeScopeTarget} | {documentId: string}) {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  const sha = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, '0')).join('');
  const canonicalTarget = 'displayName' in target ? {displayName:target.displayName.trim().replace(/\s+/gu,' '),
    scope:{kind:target.scope.kind,client_account_id:target.scope.client_account_id}} : target;
  return pendingWrite('material-upload', enterpriseId, subject,
    JSON.stringify({target:canonicalTarget, filename:filename(file), size:file.size, contentType:file.type.split(';')[0].trim().toLowerCase(), sha}));
}

const filename = (file: File) => file.name.normalize('NFC').trim();
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export function validateVersionReceipt(value: VersionSummary, file: File, documentId: string): void {
  if (!value || !uuid(value.id) || value.document_id !== documentId
    || value.original_filename !== filename(file) || value.size_bytes !== file.size) {
    throw new IngestionApiError(0, 'INVALID_RESPONSE', true);
  }
}
export function validateDocumentReceipt(value: DocumentDetail, file: File, scope: KnowledgeScopeTarget): void {
  if (!value || !uuid(value.id) || value.knowledge_scope?.kind !== scope.kind
    || value.knowledge_scope.client_account_id !== scope.client_account_id || !Array.isArray(value.versions)) {
    throw new IngestionApiError(0, 'INVALID_RESPONSE', true);
  }
  // A replay can follow a newer version. Do not mistake latest_version for the
  // version originally received; validate the returned document's history.
  const version = value.versions.find(x => x.original_filename === filename(file) && x.size_bytes === file.size);
  if (!version) throw new IngestionApiError(0, 'INVALID_RESPONSE', true);
  validateVersionReceipt(version, file, value.id);
}
