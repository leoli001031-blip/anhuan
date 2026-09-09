// Location comes from authenticated source metadata; never infer Office pages.
export function citationPosition(row: Record<string, unknown>): {pageNumber: number | null; location?: string; locator?: Record<string, unknown>; evidenceRevisionId?: string} {
  const bad = (): never => { throw new Error('EVIDENCE_LOCATION_INVALID'); };
  const positive = (x: unknown, max = 1000000): x is number => typeof x === 'number' && Number.isInteger(x) && x > 0 && x <= max;
  const uuid = (x: unknown): x is string => typeof x === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
  const sha = (x: unknown): x is string => typeof x === 'string' && /^[0-9a-f]{64}$/.test(x);
  if (row.locator === undefined || row.locator === null) {
    if (!positive(row.page_number)) return bad();
    return {pageNumber: row.page_number};
  }
  if (typeof row.locator !== 'object' || Array.isArray(row.locator) || !uuid(row.evidence_revision_id)) return bad();
  const l = row.locator as Record<string, unknown>;
  const keys = (...fields: string[]) => Object.keys(l).length === fields.length + 2 && Object.keys(l).every(k => ['schema_version','kind',...fields].includes(k));
  if (l.schema_version !== 2) return bad();
  let location: string;
  if (l.kind === 'pdf_page') {
    if (!keys('page_number') || !positive(l.page_number) || row.page_number !== l.page_number) return bad();
    location = `第 ${l.page_number} 页`;
  } else {
    if (row.page_number !== null) return bad();
    if (l.kind === 'docx_block') {
      if (!keys('part','body_index','row_index','cell_index','grid_column','grid_span','paragraph_index') || l.part !== 'word/document.xml' || !positive(l.body_index)) return bad();
      const positions = [l.row_index,l.cell_index,l.grid_column,l.grid_span,l.paragraph_index];
      if (positions.every(x => x === null)) location = `正文块 ${l.body_index} · 段落`;
      else {
        if (!positions.every(x => positive(x)) || Number(l.grid_column) < Number(l.cell_index) || Number(l.grid_column) + Number(l.grid_span) > 1000001) return bad();
        location = `正文块 ${l.body_index} · 表格第 ${l.row_index} 行第 ${l.grid_column} 列 · 段落 ${l.paragraph_index}`;
      }
    } else if (l.kind === 'xlsx_cells') {
      if (!keys('sheet_id','sheet_name','part','cell_range') || !positive(l.sheet_id,4294967295) || typeof l.sheet_name !== 'string' || !l.sheet_name || l.sheet_name.length > 31 || /[\\/?*:[\]\x00-\x1f]/.test(l.sheet_name) || l.sheet_name.startsWith("'") || l.sheet_name.endsWith("'") || typeof l.part !== 'string' || !/^xl\/worksheets\/[A-Za-z0-9_-]+\.xml$/.test(l.part) || typeof l.cell_range !== 'string') return bad();
      const cells = l.cell_range.split(':');
      const parsed = cells.map(c => {const m = /^([A-Z]{1,3})([1-9][0-9]{0,6})$/.exec(c); if (!m) return bad(); return [Number(m[2]), [...m[1]].reduce((n,ch) => n * 26 + ch.charCodeAt(0) - 64,0)];});
      if (![1,2].includes(parsed.length) || parsed.some(([r,c]) => r > 1048576 || c > 16384) || (parsed.length === 2 && (cells[0] === cells[1] || parsed[0][0] > parsed[1][0] || parsed[0][1] > parsed[1][1]))) return bad();
      location = `${l.sheet_name} · ${l.cell_range}`;
    } else if (l.kind === 'image') {
      if (!keys('source_width','source_height','rendered_width','rendered_height','rendered_sha256','exif_orientation') || ![l.source_width,l.source_height,l.rendered_width,l.rendered_height].every(x => positive(x,10000)) || Number(l.source_width)*Number(l.source_height)>40000000 || Number(l.rendered_width)*Number(l.rendered_height)>40000000 || !positive(l.exif_orientation,8) || !sha(l.rendered_sha256)) return bad();
      location = '图像全文';
    } else return bad();
  }
  if (row.location !== undefined && row.location !== location) return bad();
  return {pageNumber: row.page_number as number | null, locator:l, location, evidenceRevisionId:row.evidence_revision_id};
}
