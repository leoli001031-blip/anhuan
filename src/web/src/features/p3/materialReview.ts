import { IngestionApiError } from './ingestionApi';
export interface ReviewEntry {
  id: string; base_fragment_id: string; ordinal: number; entry_kind: 'text' | 'field';
  field_name: string | null; locator: Record<string, unknown>; location: string; text: string; body_sha256: string;
}
export interface MaterialReview {
  version_id: string; source_format: 'pdf' | 'docx' | 'xlsx' | 'jpeg'; source_sha256: string;
  base_revision_id: string | null; base_manifest_sha256: string | null; editable: boolean;
  review_head: {id: string; action: 'confirm' | 'revoke'; revision_no: number; base_revision_id: string | null; base_manifest_sha256: string | null} | null;
  base_items: ReviewEntry[]; review_items: ReviewEntry[];
}
export interface ReviewWrite {
  request_id: string; expected_review_revision_id: string | null; action: 'confirm' | 'revoke';
  base_revision_id: string | null; base_manifest_sha256: string | null; checked_against_source: boolean;
  texts: {base_fragment_id: string; text: string}[];
  fields: {base_fragment_id: string; field_name: string; text: string}[];
}
export interface ReviewReceipt {request_id: string; id: string; revision_no: number; action: 'confirm' | 'revoke'; replayed: boolean}
const uuid = (x: unknown): x is string => typeof x === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(x);
const sha = (x: unknown): x is string => typeof x === 'string' && /^[0-9a-f]{64}$/.test(x);
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const fail = (): never => {throw new IngestionApiError(0, 'INVALID_RESPONSE', true)};
export function parseMaterialReview(raw: unknown, versionId: string): MaterialReview {
  if (!object(raw) || raw.version_id !== versionId || !['pdf','docx','xlsx','jpeg'].includes(String(raw.source_format)) || !sha(raw.source_sha256)
    || typeof raw.editable !== 'boolean' || !(raw.base_revision_id === null || uuid(raw.base_revision_id))
    || !(raw.base_manifest_sha256 === null || sha(raw.base_manifest_sha256))) return fail();
  for (const key of ['base_items','review_items']) {
    const items = raw[key];
    if (!Array.isArray(items) || items.length > 20015) return fail();
    for (const [n, item] of items.entries()) {
      if (!object(item) || !uuid(item.id) || !uuid(item.base_fragment_id) || item.ordinal !== n
        || !['text','field'].includes(String(item.entry_kind)) || !(item.field_name === null || typeof item.field_name === 'string')
        || !object(item.locator) || typeof item.location !== 'string' || typeof item.text !== 'string' || !sha(item.body_sha256)) return fail();
    }
  }
  const head = raw.review_head;
  if (head !== null && (!object(head) || !uuid(head.id) || !['confirm','revoke'].includes(String(head.action))
    || !Number.isInteger(head.revision_no) || Number(head.revision_no) < 1
    || !(head.base_revision_id === null || uuid(head.base_revision_id)) || !(head.base_manifest_sha256 === null || sha(head.base_manifest_sha256)))) return fail();
  if (raw.editable && (!raw.base_revision_id || !raw.base_manifest_sha256 || !(raw.base_items as unknown[]).length)) return fail();
  return raw as unknown as MaterialReview;
}
export function parseReviewReceipt(raw: unknown, request: ReviewWrite): ReviewReceipt {
  if (!object(raw) || raw.request_id !== request.request_id || !uuid(raw.id) || raw.action !== request.action
    || !Number.isInteger(raw.revision_no) || Number(raw.revision_no) < 1 || typeof raw.replayed !== 'boolean') return fail();
  return raw as unknown as ReviewReceipt;
}
export const REVIEW_FIELDS = [
  ['source_title','来源标题'],['publisher','发布机构'],['source_type','来源类型'],['jurisdiction','适用地区'],
  ['source_reference','文件编号'],['version_title','版本标题'],['domain','业务领域'],['effect_status','效力状态'],
  ['issued_on','发布日期'],['effective_from','生效日期'],['effective_to','失效日期'],['summary','内容摘要'],
  ['report_title','报告标题'],['report_date','报告日期'],['report_summary','报告摘要'],
] as const;
